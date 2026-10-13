"""
Job Queue 完整測試。用 FakeSleepRunner（本機 sleep 指令）模擬 starccm+，
duration 設短一點，這樣可以在幾十秒內測完整個排程演算法、覆蓋保護、
以及「backend 重啟後佇列不消失」這三件事。

跑法：
    cd backend
    python3 -m app.jobs.test_queue
"""

import sys
import time
import shutil
import subprocess
import os
import tempfile
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.test_support import use_temp_data_dir
use_temp_data_dir()   # never touch the real data/ directory

from app.jobs.models import CoreMode, JobStatus
from app.jobs.runner import FakeSleepRunner
from app.jobs.queue_manager import JobQueueManager

TEST_DIR = Path(tempfile.gettempdir()) / "aero404_queue_test"
STORAGE_PATH = TEST_DIR / "jobs.json"
WORK_DIR_ROOT = TEST_DIR / "cases"


class VariableSleepRunner(FakeSleepRunner):
    """
    測試專用：不同 case_name 可以指定不同的模擬時長，
    用來製造「某個 job 先結束、另一個還在跑」的中間狀態方便驗證，
    正常 production 用的 runner 不會有這個概念（starccm+ 沒有固定 duration）。
    """

    def __init__(self, default_duration: int = 5, overrides: dict[str, int] | None = None):
        super().__init__(duration_sec=default_duration)
        self.overrides = overrides or {}

    def start(self, job_id: str, cores: int, work_dir: Path, command: Optional[str] = None) -> int:
        case_name = work_dir.name
        duration = self.overrides.get(case_name, self.duration_sec)

        work_dir.mkdir(parents=True, exist_ok=True)
        log_path = work_dir / "workflow.log"
        with log_path.open("w") as log_file:
            sleep_command = [sys.executable, "-c", f"import time; time.sleep({duration})"]
            proc = subprocess.Popen(
                sleep_command,
                stdout=log_file,
                stderr=log_file,
                cwd=str(work_dir),
            )
        self._processes[proc.pid] = proc
        (work_dir / "run.pid").write_text(str(proc.pid))
        return proc.pid


def reset_test_dir():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)


def wait_until(predicate, timeout=15, interval=0.3, poll_fn=None):
    start = time.time()
    while time.time() - start < timeout:
        if poll_fn:
            poll_fn()
        if predicate():
            return True
        time.sleep(interval)
    return False


def print_status(mgr, label):
    print(f"\n--- {label} ---")
    for j in sorted(mgr.list_jobs(), key=lambda x: x.created_at):
        print(f"  [{j.server_name}] {j.case_name:20s} mode={j.mode.value:5s} "
              f"status={j.status.value:8s} cores={j.cores_allocated} pid={j.pid}")


def test_1_half_mode_scheduling():
    print("\n========================================")
    print("測試 1：half 模式 — 固定半數分配（不是遞減式）")
    print("========================================")
    reset_test_dir()

    # ServerA: total=64, reserved=2 → max=62 → half=31
    # job1 先結束（3秒），job2 撐比較久（15秒），這樣才能觀察到
    # 「job1 結束、job3 補位、job2 全程沒受影響」這個中間狀態
    runner = VariableSleepRunner(default_duration=3, overrides={"TH12_002": 15})
    mgr = JobQueueManager(runner, STORAGE_PATH, WORK_DIR_ROOT)

    j1 = mgr.add_job("TH12_001", "ServerA", CoreMode.HALF, "admin")
    print_status(mgr, "新增第一個 half job")
    assert j1.status == JobStatus.SOLVING, "第一個 half job 應該立刻開始"
    assert j1.cores_allocated == 31, f"62 的一半應該是 31，實際是 {j1.cores_allocated}"
    print(f"  ✅ Job1 立刻開始，cores={j1.cores_allocated}（預期 31）")

    j2 = mgr.add_job("TH12_002", "ServerA", CoreMode.HALF, "user_a")
    print_status(mgr, "新增第二個 half job")
    assert j2.status == JobStatus.SOLVING, "還有剩餘核心正好等於固定半數，第二個 half job 也該立刻開始"
    assert j2.cores_allocated == 31, f"half 是固定值，應該也是 31，實際是 {j2.cores_allocated}"
    print(f"  ✅ Job2 立刻開始，cores={j2.cores_allocated}（固定 31，不是遞減式的 15）")

    print(f"\n  目前 ServerA used_cores={mgr.used_cores('ServerA')}, available={mgr.available_cores('ServerA')}")
    assert mgr.available_cores("ServerA") == 0, "兩個 half job 應該正好把 62 顆吃滿"

    j3 = mgr.add_job("TH12_003", "ServerA", CoreMode.HALF, "user_b")
    print_status(mgr, "兩個 half 都在跑時，新增第三個 half job")
    assert j3.status == JobStatus.WAITING, "沒有剩餘核心可以塞進固定半數，第三個該進 WAITING"
    print("  ✅ Job3 正確進入 WAITING（62 顆已滿）")

    print("\n  等待 job1 結束，觀察 job3 是否自動補位（維持『兩個兩個』）...")
    ok = wait_until(
        lambda: mgr.get_job(j3.id).status == JobStatus.SOLVING,
        timeout=15,
        poll_fn=mgr.poll,
    )
    assert ok, "job1 結束釋放 31 顆後，job3 應該自動補位開始跑"
    print_status(mgr, "job1 結束後")
    j1_final = mgr.get_job(j1.id)
    j3_refreshed = mgr.get_job(j3.id)
    assert j1_final.status == JobStatus.DONE
    assert j3_refreshed.cores_allocated == 31
    assert mgr.get_job(j2.id).status == JobStatus.SOLVING, "job2 應該完全不受影響，全程繼續跑"
    print(f"  ✅ Job3 自動補位，維持『兩個在跑』的狀態，job2 全程未受影響")

    for j in mgr.list_jobs(status=JobStatus.SOLVING):
        if j.pid:
            mgr.runner.kill(j.pid)


def test_2_all_mode_waits():
    print("\n========================================")
    print("測試 2：all 模式必須等『所有』job 完全結束，且不會被半模式餓死")
    print("========================================")
    reset_test_dir()

    runner = VariableSleepRunner(default_duration=4, overrides={"TH12_010": 2, "TH12_012": 10})
    mgr = JobQueueManager(runner, STORAGE_PATH, WORK_DIR_ROOT)

    j1 = mgr.add_job("TH12_010", "ServerA", CoreMode.HALF, "admin")
    assert j1.status == JobStatus.SOLVING
    print(f"  Job1 (half) 開始跑，cores={j1.cores_allocated}")

    j2 = mgr.add_job("TH12_011", "ServerA", CoreMode.ALL, "user_a")
    print_status(mgr, "有 job1 在跑時新增 all job")
    assert j2.status == JobStatus.WAITING, "all 模式在有其他 job 跑時應該進 WAITING"
    print("  ✅ all 模式正確卡在 WAITING（因為 ServerA 上還有 job1 在跑）")

    j3 = mgr.add_job("TH12_012", "ServerA", CoreMode.HALF, "user_b")
    print_status(mgr, "同時新增第三個 half job（測試不會被卡住的 all job 擋住）")
    assert j3.status == JobStatus.SOLVING, "half job 不該被排在前面的 all job 擋住（跳過去執行）"
    assert j3.cores_allocated == 31, "第二個 half 一樣拿固定的 31，正好把 62 顆吃滿"
    print("  ✅ half job 正確跳過卡住的 all job，直接開始執行，剛好用完剩下的固定半數")

    print("\n  等待 job1 結束（job3 仍在跑），確認 all job 還是繼續等...")
    wait_until(lambda: mgr.get_job(j1.id).status == JobStatus.DONE, timeout=15, poll_fn=mgr.poll)
    print_status(mgr, "job1 結束、job3 仍在跑")
    assert mgr.get_job(j2.id).status == JobStatus.WAITING, "job3 還在佔用核心，all job 必須繼續等到完全淨空"
    print("  ✅ 正確：只有 job1 結束，job3 還在跑，all job 依然 WAITING（不是只等第一個而已）")

    print("\n  繼續等 job3 也結束，確認 all job 這時才真正開始...")
    ok = wait_until(
        lambda: mgr.get_job(j2.id).status == JobStatus.SOLVING,
        timeout=15,
        poll_fn=mgr.poll,
    )
    assert ok, "job1、job3 都結束後，all job 應該自動被排程開始"
    print_status(mgr, "job1、job3 都結束後")
    j2_refreshed = mgr.get_job(j2.id)
    assert j2_refreshed.cores_allocated == 62, "all 模式應該拿走此時全部可用核心"
    print(f"  ✅ all job 在完全淨空後才開始，cores={j2_refreshed.cores_allocated}")

    for j in mgr.list_jobs(status=JobStatus.SOLVING):
        if j.pid:
            mgr.runner.kill(j.pid)


def test_3_two_and_two_refill():
    print("\n========================================")
    print("測試 3：一個跑完，下一個自動接上（維持佇列流動）")
    print("========================================")
    reset_test_dir()

    runner = FakeSleepRunner(duration_sec=3)
    mgr = JobQueueManager(runner, STORAGE_PATH, WORK_DIR_ROOT)

    j1 = mgr.add_job("TH12_020", "ServerB", CoreMode.ALL, "admin")
    assert j1.status == JobStatus.SOLVING
    print(f"  Job1 (all) 開始跑，cores={j1.cores_allocated}（ServerB total=32, reserved=4 → 28）")
    assert j1.cores_allocated == 28

    j2 = mgr.add_job("TH12_021", "ServerB", CoreMode.ALL, "user_a")
    assert j2.status == JobStatus.WAITING
    print("  Job2 (all) 進 WAITING，等 job1 結束")

    ok = wait_until(
        lambda: mgr.get_job(j2.id).status == JobStatus.SOLVING,
        timeout=15,
        poll_fn=mgr.poll,
    )
    assert ok
    print_status(mgr, "job1 完成後")
    j1_final = mgr.get_job(j1.id)
    assert j1_final.status == JobStatus.DONE, "正常結束（exit code 0）應該標記 DONE"
    print(f"  ✅ Job1 正確標記為 DONE，Job2 自動接上開始跑")

    for j in mgr.list_jobs(status=JobStatus.SOLVING):
        if j.pid:
            mgr.runner.kill(j.pid)


def test_4_overwrite_protection():
    print("\n========================================")
    print("測試 4：覆蓋保護規則")
    print("========================================")
    reset_test_dir()

    runner = FakeSleepRunner(duration_sec=6)
    mgr = JobQueueManager(runner, STORAGE_PATH, WORK_DIR_ROOT)

    allowed, reason = mgr.check_can_overwrite("brand_new_case")
    assert allowed is True and reason == ""
    print(f"  ✅ 全新名稱：允許，無警告")

    j1 = mgr.add_job("TH12_030", "ServerA", CoreMode.HALF, "admin")
    assert j1.status == JobStatus.SOLVING

    allowed, reason = mgr.check_can_overwrite("TH12_030")
    assert allowed is False
    print(f"  ✅ SOLVING 中的 case：禁止覆蓋 → {reason}")

    mgr.kill_job(j1.id)
    ok = wait_until(lambda: mgr.get_job(j1.id).status == JobStatus.FAILED, timeout=10, poll_fn=mgr.poll)
    assert ok, "kill 之後 poll 應該偵測到 process 死亡並標記 FAILED"
    print(f"  Job1 被 kill 後狀態：{mgr.get_job(j1.id).status.value}, killed={mgr.get_job(j1.id).killed}")

    allowed, reason = mgr.check_can_overwrite("TH12_030")
    assert allowed is True
    print(f"  ✅ FAILED（已被 kill）的 case：允許覆蓋，但有警告 → {reason}")


def test_5_persistence_across_restart():
    print("\n========================================")
    print("測試 5：backend 重啟後佇列不消失")
    print("========================================")
    reset_test_dir()

    runner1 = FakeSleepRunner(duration_sec=8)
    mgr1 = JobQueueManager(runner1, STORAGE_PATH, WORK_DIR_ROOT)

    j1 = mgr1.add_job("TH12_040", "ServerA", CoreMode.HALF, "admin")
    j2 = mgr1.add_job("TH12_041", "ServerA", CoreMode.ALL, "user_a")  # 會卡 WAITING
    print_status(mgr1, "重啟前")
    assert j1.status == JobStatus.SOLVING
    assert j2.status == JobStatus.WAITING

    print("\n  模擬 backend 重啟：用新的 JobQueueManager 實例讀同一份 storage...")
    # 注意：這裡故意重用同一個 runner 物件 的底層 process 還活著，
    # 但 _processes dict 是新的 runner2 沒有記憶，模擬「重啟後記憶體清空，
    # 只能靠 pid 檢查」的真實情境
    runner2 = FakeSleepRunner(duration_sec=8)
    mgr2 = JobQueueManager(runner2, STORAGE_PATH, WORK_DIR_ROOT)

    j1_reloaded = mgr2.get_job(j1.id)
    j2_reloaded = mgr2.get_job(j2.id)

    assert j1_reloaded is not None, "重啟後 job1 紀錄應該還在"
    assert j2_reloaded is not None, "重啟後 job2 紀錄應該還在"
    print_status(mgr2, "重啟後（新的 manager 實例讀同一份 jobs.json）")

    # job1 的 pid 實際上還活著（sleep 8 秒還沒結束），用系統層級 os.kill(pid,0) 應該能偵測到
    assert j1_reloaded.status == JobStatus.SOLVING, "pid 還活著，重啟後應該繼續信任這個 job 在跑"
    print(f"  ✅ Job1 pid={j1_reloaded.pid} 仍存活，重啟後正確保持 SOLVING 狀態（沒有誤判成失敗）")

    assert j2_reloaded.status == JobStatus.WAITING, "WAITING 的 job 重啟後應該原封不動"
    print(f"  ✅ Job2 (WAITING) 重啟後狀態不變")

    # 清理：殺掉還在跑的 sleep process
    if j1_reloaded.pid:
        runner2.kill(j1_reloaded.pid)


def test_6_start_failure_does_not_crash_and_marks_failed():
    print("\n========================================")
    print("測試 6：啟動失敗（例如 SSH 連不上）時，job 要正確標記 FAILED 並附上原因")
    print("========================================")
    reset_test_dir()

    class BrokenRunner(FakeSleepRunner):
        """模擬 SSH 連線失敗：start() 直接噴例外。"""
        def start(self, job_id, cores, work_dir, command=None, server_name=None):
            raise RuntimeError("ssh: connect to host 192.0.2.10 port 22: Connection refused")

    runner = BrokenRunner(duration_sec=3)
    mgr = JobQueueManager(runner, STORAGE_PATH, WORK_DIR_ROOT)

    # add_job 本身不應該拋出例外
    job = mgr.add_job("TH12_FAIL", "ServerA", CoreMode.HALF, "admin")

    assert job.status == JobStatus.FAILED, f"啟動失敗應該直接標記 FAILED，實際是 {job.status.value}"
    assert job.error_message and "Connection refused" in job.error_message, \
        f"error_message 應該包含失敗原因，實際是: {job.error_message}"
    print(f"  ✅ add_job() 沒有拋出例外，job 正確標記 FAILED")
    print(f"  ✅ error_message 正確記錄原因: {job.error_message}")

    # 這個失敗的 job 不該佔用核心，後面的 job 要能正常排到
    assert mgr.used_cores("ServerA") == 0, "啟動失敗的 job 不該佔用核心"

    job2 = mgr.add_job("TH12_AFTER_FAIL", "ServerA", CoreMode.HALF, "admin")
    # ServerA 用的還是 BrokenRunner，這個也會失敗，但重點是驗證
    # 「不會被前一個失敗卡住、也不會崩潰」
    assert job2.status == JobStatus.FAILED
    print(f"  ✅ 一個 job 啟動失敗，不會擋住或搞壞後續 job 的處理")


def test_7_remove_job_and_clear_failed():
    print("\n========================================")
    print("測試 7：admin 刪除 job 紀錄 / 一鍵清除 FAILED")
    print("========================================")
    reset_test_dir()

    class BrokenRunner(FakeSleepRunner):
        def start(self, job_id, cores, work_dir, command=None, server_name=None):
            raise RuntimeError("模擬啟動失敗")

    runner = BrokenRunner(duration_sec=2)
    mgr = JobQueueManager(runner, STORAGE_PATH, WORK_DIR_ROOT)

    # 建立 2 個會失敗的 job
    fail1 = mgr.add_job("FAIL_1", "ServerA", CoreMode.HALF, "admin")
    fail2 = mgr.add_job("FAIL_2", "ServerA", CoreMode.HALF, "admin")
    assert fail1.status == JobStatus.FAILED and fail2.status == JobStatus.FAILED

    # 不能刪除還在跑/等待的 job
    good_runner = FakeSleepRunner(duration_sec=30)
    mgr2 = JobQueueManager(good_runner, STORAGE_PATH.with_name("jobs2.json"), WORK_DIR_ROOT)
    running_job = mgr2.add_job("STILL_RUNNING", "ServerA", CoreMode.HALF, "admin")
    assert running_job.status == JobStatus.SOLVING
    try:
        mgr2.remove_job(running_job.id)
        assert False, "不應該能刪除還在跑的 job"
    except ValueError as e:
        print(f"  ✅ 正確拒絕刪除 SOLVING 中的 job: {e}")
    mgr2.runner.kill(running_job.pid)

    # 個別刪除
    mgr.remove_job(fail1.id)
    assert mgr.get_job(fail1.id) is None
    assert mgr.get_job(fail2.id) is not None
    print("  ✅ 個別刪除單筆 job 紀錄成功")

    # 一鍵清除全部 FAILED
    mgr.add_job("FAIL_3", "ServerB", CoreMode.HALF, "admin")  # 換一台 server 也失敗
    removed = mgr.clear_failed()
    assert removed == 2, f"應該清掉 2 筆 FAILED（fail2, FAIL_3），實際 {removed}"
    assert len(mgr.list_jobs()) == 0
    print(f"  ✅ 一鍵清除 FAILED 成功，共清掉 {removed} 筆")


if __name__ == "__main__":
    test_1_half_mode_scheduling()
    test_2_all_mode_waits()
    test_3_two_and_two_refill()
    test_4_overwrite_protection()
    test_5_persistence_across_restart()
    test_6_start_failure_does_not_crash_and_marks_failed()
    test_7_remove_job_and_clear_failed()

    print("\n========================================")
    print("✅ 全部測試通過")
    print("========================================")
