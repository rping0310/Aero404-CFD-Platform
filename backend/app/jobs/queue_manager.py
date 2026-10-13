"""
Job Queue 管理器：核心排程邏輯 + 持久化。

排程規則（對照專案討論確認過的版本）：
  - "half"：只要該 server 目前 available_cores > 0 就能立刻開始，
            拿 available_cores 的一半（整數除法，至少 1 顆）。
  - "all"： 必須等到該 server 完全沒有其他 job 在跑（used_cores == 0）
            才能開始，拿走當時全部 available_cores。

  available_cores = total_cores - reserved_cores - used_cores
  （total_cores / reserved_cores 來自 servers.yaml，熱重載）

持久化：狀態存成 JSON 檔（data/jobs.json）。backend 重啟時：
  - SOLVING 的 job 檢查 pid 是否還活著（is_alive）
    - 活著 → 繼續監控（重新 attach）
    - 不在了 → 標記 FAILED（假設是隨著 service 重啟被中斷）
  這樣就算網頁關掉、甚至 backend 重啟，佇列本身不會憑空消失。

覆蓋保護規則：
  check_can_overwrite(case_name) 只檢查佇列系統的紀錄（WAITING/SOLVING
  禁止覆蓋，DONE/FAILED 或完全沒紀錄過才允許，但仍要在 API 層跳警告）。
  「資料夾是否存在」是磁碟層的檢查，不在這個模組的職責範圍內，
  之後接「建立 case」的 API 時，兩邊檢查要一起做。
"""

import json
import threading
from pathlib import Path
from typing import Optional

from .models import Job, JobStatus, CoreMode
from .runner import JobRunner
from ..config import settings


class JobQueueManager:

    def __init__(self, runner: JobRunner, storage_path: Path, work_dir_root: Path):
        self.runner = runner
        self.storage_path = Path(storage_path)
        self.work_dir_root = Path(work_dir_root)
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._auto_failed: dict[str, str] = {}   # LOGKILL_PATCH: job_id -> reason, kill sent, waiting for exit
        self._load()
        self._recover_on_startup()

    # ---------------------------------------------------------------- 持久化

    def _load(self) -> None:
        if not self.storage_path.exists():
            self._jobs = {}
            return
        with self.storage_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        self._jobs = {j["id"]: Job.from_dict(j) for j in raw}

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.storage_path.open("w", encoding="utf-8") as f:
            json.dump([j.to_dict() for j in self._jobs.values()], f, ensure_ascii=False, indent=2)

    def _recover_on_startup(self) -> None:
        """重啟後，檢查所有標記 SOLVING 的 job，pid 還活著才繼續信任它。"""
        changed = False
        for job in self._jobs.values():
            if job.status == JobStatus.SOLVING:
                if job.pid is None or not self._runner_is_alive(job):
                    job.status = JobStatus.FAILED
                    job.error_message = "Process no longer existed after service restart"
                    changed = True
        if changed:
            self._save()

    # ---------------------------------------------------------------- 查詢

    def _server_config(self, server_name: str) -> dict:
        for s in settings.get_servers():
            if s["name"] == server_name:
                return s
        raise ValueError(f"Server '{server_name}' not found in configuration")

    def used_cores(self, server_name: str) -> int:
        return sum(
            j.cores_allocated or 0
            for j in self._jobs.values()
            if j.server_name == server_name and j.status == JobStatus.SOLVING
        )

    def available_cores(self, server_name: str) -> int:
        cfg = self._server_config(server_name)
        return cfg["total_cores"] - cfg["reserved_cores"] - self.used_cores(server_name)

    def list_jobs(self, server_name: Optional[str] = None, status: Optional[JobStatus] = None) -> list[Job]:
        jobs = list(self._jobs.values())
        if server_name is not None:
            jobs = [j for j in jobs if j.server_name == server_name]
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        return jobs

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def check_can_overwrite(self, case_name: str) -> tuple[bool, str]:
        """
        回傳 (allowed, reason)。
        只檢查佇列紀錄；磁碟資料夾是否存在由呼叫端另外檢查。
        """
        for job in self._jobs.values():
            if job.case_name == case_name and job.status in (JobStatus.WAITING, JobStatus.SOLVING):
                return False, f"Case '{case_name}' is currently {job.status.value}, cannot overwrite while running"

        existing = [j for j in self._jobs.values() if j.case_name == case_name]
        if existing:
            latest = max(existing, key=lambda j: j.created_at)
            return True, f"Case '{case_name}' already exists (status: {latest.status.value}). Overwrite it?"

        return True, ""

    # ---------------------------------------------------------------- 新增 / 排程

    def add_job(self, case_name: str, server_name: str, mode: CoreMode, added_by: str,
                command_template: Optional[str] = None, work_dir: Optional[str] = None,
                simulation_type: Optional[int] = None, note: Optional[str] = None) -> Job:
        with self._lock:
            existing_waiting = [j for j in self._jobs.values() if j.server_name == server_name and j.status == JobStatus.WAITING]
            job = Job(
                case_name=case_name,
                server_name=server_name,
                mode=mode,
                added_by=added_by,
                command_template=command_template,
                work_dir=work_dir,
                simulation_type=simulation_type,
                note=note,
                queue_position=len(existing_waiting),
            )
            self._jobs[job.id] = job
            self._try_schedule(server_name)
            self._save()
            return job

    def _try_schedule(self, server_name: str) -> None:
        """
        該 server 的排程核心：依 queue_position 順序檢查 WAITING job，
        能跑的就開始跑，跑不了的先跳過（不卡住後面能跑的）。

        「half」是這台 server 固定的半數（max_cores // 2），每次都拿一樣的
        數字，不會因為目前已經有人在跑就縮水；「all」則必須等這台 server
        完全沒有其他 job 在跑（used_cores == 0）才能開始，拿走當時全部
        available_cores。

        這樣兩個 half job 同時跑，會各佔 max_cores 的一半，正好把整台吃滿
        （例如 62 顆 → 31 + 31），維持「兩個兩個」的分配方式，而不是
        越分越少。呼叫端要自己包 lock（add_job / poll 都已經包了）。
        """
        waiting = sorted(
            [j for j in self._jobs.values() if j.server_name == server_name and j.status == JobStatus.WAITING],
            key=lambda j: j.queue_position,
        )

        for job in waiting:
            cfg = self._server_config(server_name)
            max_cores = cfg["total_cores"] - cfg["reserved_cores"]
            half_cores = max_cores // 2
            used = self.used_cores(server_name)
            available = max_cores - used

            if job.mode == CoreMode.ALL:
                if used > 0:
                    continue  # 還有別的在跑，all 模式必須等它們全部結束
                cores_to_use = available  # 此時 == max_cores
            else:  # HALF
                if available < half_cores:
                    continue  # 剩餘空間不夠塞進固定的一半，先跳過等下次機會
                cores_to_use = half_cores

            try:
                self._start_job(job, cores_to_use)
            except Exception as exc:
                from datetime import datetime
                job.status = JobStatus.FAILED
                job.finished_at = datetime.now().isoformat()
                job.error_message = f"Failed to start: {exc}"

    def _start_job(self, job: Job, cores: int) -> None:
        from datetime import datetime

        work_dir = Path(job.work_dir) if job.work_dir else self.work_dir_root / job.case_name

        # 填入實際核心數
        actual_command = None
        if job.command_template:
            actual_command = job.command_template.replace("{{cores}}", str(cores))

        try:
            pid = self.runner.start(
                job.id, cores, work_dir, command=actual_command,
                server_name=job.server_name,
            )
        except TypeError as exc:
            # Backward compatibility for third-party/test runners written against v0.1.
            if "server_name" not in str(exc):
                raise
            pid = self.runner.start(job.id, cores, work_dir, command=actual_command)

        job.cores_allocated = cores
        job.pid = pid
        job.status = JobStatus.SOLVING
        job.started_at = datetime.now().isoformat()

    # ---- LOGKILL_PATCH ----
    # Default rule. Override (hot-reloaded, no restart) in app_settings.yaml:
    #   fail_log_rules:
    #     - match: 'Named object "domain.inlet" does not exist in manager'
    #       reason: "No inlet boundary (CAD hole)"
    # NOTE: once fail_log_rules is present it REPLACES this default list.
    DEFAULT_FAIL_LOG_RULES = [
        {
            "match": 'Named object "domain.inlet" does not exist in manager',
            "reason": "No inlet boundary (CAD likely has a hole, domain.inlet missing) - stopped early",
        },
    ]

    def _fail_rules(self) -> list:
        rules = settings.get_app_settings().get("fail_log_rules")
        return rules if isinstance(rules, list) and rules else self.DEFAULT_FAIL_LOG_RULES

    def _check_log_failure(self, job: Job) -> Optional[str]:
        find = getattr(self.runner, "find_in_log", None)
        if find is None:
            return None
        rules = [r for r in self._fail_rules() if isinstance(r, dict) and r.get("match")]
        if not rules:
            return None
        hit = find([r["match"] for r in rules], server_name=job.server_name,
                   work_dir=Path(job.work_dir) if job.work_dir else None)
        if not hit:
            return None
        for r in rules:
            if r["match"] == hit:
                return r.get("reason") or f"Log matched: {hit}"
        return f"Log matched: {hit}"
    # ---- /LOGKILL_PATCH ----

    # ---------------------------------------------------------------- 狀態更新（由外部輪詢呼叫）

    def poll(self) -> None:
        """
        外部（之後會是背景 asyncio task，依 app_settings 的
        status_poll_interval_sec 週期呼叫）定期呼叫這個方法，
        檢查所有 SOLVING 的 job 是否已結束，結束了就釋放核心、
        觸發下一輪排程。
        """
        with self._lock:
            from datetime import datetime

            servers_to_reschedule = set()

            for job in list(self._jobs.values()):
                if job.status != JobStatus.SOLVING or job.pid is None:
                    continue

                if self._runner_is_alive(job):  # LOGKILL_PATCH
                    # still running: if workflow.log hits a fail rule, stop it now (do not waste compute)
                    if not job.killed and job.id not in self._auto_failed:
                        reason = self._check_log_failure(job)
                        if reason:
                            self._auto_failed[job.id] = reason
                            job.error_message = reason
                            self.runner.kill(job.pid, server_name=job.server_name,
                                             work_dir=Path(job.work_dir) if job.work_dir else None)
                            self._save()
                    continue

                return_code = self._runner_return_code(job)
                job.finished_at = datetime.now().isoformat()
                auto_reason = self._auto_failed.pop(job.id, None)
                if auto_reason is None and not job.killed:
                    # service restarted meanwhile, or job finished between two polls: scan once more
                    auto_reason = self._check_log_failure(job)
                if auto_reason:
                    job.status = JobStatus.FAILED
                    job.error_message = auto_reason
                elif job.killed:
                    job.status = JobStatus.FAILED
                elif return_code == 0:
                    job.status = JobStatus.DONE
                else:
                    job.status = JobStatus.FAILED
                    job.error_message = f"Exit code: {return_code}"
                servers_to_reschedule.add(job.server_name)

            for server_name in servers_to_reschedule:
                self._try_schedule(server_name)

            if servers_to_reschedule:
                self._save()

    # ---------------------------------------------------------------- Admin 操作

    def kill_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            if job.status != JobStatus.SOLVING:
                raise ValueError(f"Job {job_id} is currently {job.status.value}, not SOLVING, cannot kill")

            if job.pid is not None:
                self.runner.kill(job.pid, server_name=job.server_name,
                                 work_dir=Path(job.work_dir) if job.work_dir else None)
            job.killed = True
            self._save()
            # 實際狀態轉移交給下一次 poll() 偵測 process 死亡後處理，
            # 這裡先不強制設 FAILED，避免跟 poll() 的邏輯打架。

    def reorder_queue(self, server_name: str, ordered_job_ids: list[str]) -> None:
        """Admin 調整某台 server 上 WAITING job 的順序。"""
        with self._lock:
            for position, job_id in enumerate(ordered_job_ids):
                job = self._jobs.get(job_id)
                if job is not None and job.server_name == server_name and job.status == JobStatus.WAITING:
                    job.queue_position = position
            self._save()

    def remove_job(self, job_id: str) -> None:
        """
        Admin 從歷史紀錄裡永久刪除一筆 job（不是 kill，是把整筆紀錄砍掉）。
        只允許刪除 DONE / FAILED 的 job —— 還在 WAITING / SOLVING 的必須先用
        kill_job() 停掉，不能直接刪，避免刪掉一個實際上還在跑的 process 的紀錄
        導致核心數算錯、或留下孤兒 process 沒人管。
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            if job.status == JobStatus.SOLVING:
                raise ValueError(f"Job {job_id} is currently {job.status.value}, please kill it before deleting")
            del self._jobs[job_id]
            self._save()

    def clear_failed(self, server_name: Optional[str] = None) -> int:
        """
        一鍵清除所有 FAILED 的 job 紀錄（Job Session 太多失敗紀錄時用）。
        可選擇只清特定 server 的，不給的話清全部。回傳實際清掉幾筆。
        """
        with self._lock:
            to_remove = [
                jid for jid, j in self._jobs.items()
                if j.status == JobStatus.FAILED and (server_name is None or j.server_name == server_name)
            ]
            for jid in to_remove:
                del self._jobs[jid]
            if to_remove:
                self._save()
            return len(to_remove)

    def set_note(self, job_id: str, note: str) -> None:
        """更新一筆 job 的備註，任何登入的使用者都能編輯（純資訊性質，不是
        危險操作，不需要限制成 admin only）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            job.note = note
            self._save()

    def _runner_is_alive(self, job: Job) -> bool:
        return self.runner.is_alive(
            job.pid,
            server_name=job.server_name,
            work_dir=Path(job.work_dir) if job.work_dir else None,
        )

    def _runner_return_code(self, job: Job) -> Optional[int]:
        return self.runner.get_return_code(
            job.pid,
            server_name=job.server_name,
            work_dir=Path(job.work_dir) if job.work_dir else None,
        )
