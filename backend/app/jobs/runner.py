"""
Job Runner 抽象層。

這個檔案是「怎麼真正啟動一個模擬」的實作，跟 queue_manager 的排程邏輯
完全分開 —— queue_manager 只知道「叫 runner 開始跑」、「問 runner 這個
job 還活著嗎」、「叫 runner 殺掉這個 job」，不管實際上是本機 subprocess
還是 SSH 到遠端。

FakeSleepRunner：測試用，本機執行 `sleep N`，模擬 starccm+ 跑很久。
之後接真正的 STAR-CCM+ 時，只要換成 RealRunner（本機 subprocess 或
SSH nohup + pid 檔案，依 server 設定決定走哪條），queue_manager 完全不用改。
"""

import subprocess
import os
import signal
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class JobRunner(ABC):
    @abstractmethod
    def start(self, job_id: str, cores: int, work_dir: Path, command: Optional[str] = None,
              server_name: Optional[str] = None) -> int:
        """啟動 process，回傳 pid。command 是完整指令字串（已填入 cores）。"""
        ...

    @abstractmethod
    def is_alive(self, pid: int, server_name: Optional[str] = None,
                 work_dir: Optional[Path] = None) -> bool:
        """process 是否還在跑。"""
        ...

    @abstractmethod
    def get_return_code(self, pid: int, server_name: Optional[str] = None,
                        work_dir: Optional[Path] = None) -> Optional[int]:
        """process 結束後的 exit code；還在跑回傳 None。"""
        ...

    @abstractmethod
    def kill(self, pid: int, server_name: Optional[str] = None,
             work_dir: Optional[Path] = None) -> None:
        """強制終止。"""
        ...


class FakeSleepRunner(JobRunner):
    """
    測試用：不真的跑 starccm+，改成 `sleep <duration>`。
    即使傳了 command 也忽略，永遠跑 sleep。
    """

    def __init__(self, duration_sec: int = 5):
        self.duration_sec = duration_sec
        self._processes: dict[int, subprocess.Popen] = {}

    def start(self, job_id: str, cores: int, work_dir: Path, command: Optional[str] = None,
              server_name: Optional[str] = None) -> int:
        work_dir.mkdir(parents=True, exist_ok=True)
        log_path = work_dir / "workflow.log"

        with log_path.open("w") as log_file:
            # 把 command 記在 log 裡方便測試驗證，但實際跑 sleep
            if command:
                log_file.write(f"[TEST] Would execute: {command}\n")
                log_file.flush()

            sleep_command = [sys.executable, "-c", f"import time; time.sleep({self.duration_sec})"]
            proc = subprocess.Popen(
                sleep_command,
                stdout=log_file,
                stderr=log_file,
                cwd=str(work_dir),
            )

        self._processes[proc.pid] = proc
        (work_dir / "run.pid").write_text(str(proc.pid))
        return proc.pid

    def is_alive(self, pid: int, server_name: Optional[str] = None,
                 work_dir: Optional[Path] = None) -> bool:
        proc = self._processes.get(pid)
        if proc is not None:
            return proc.poll() is None
        # 不在記憶體裡（例如程式重啟後）：用系統層級檢查
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def get_return_code(self, pid: int, server_name: Optional[str] = None,
                        work_dir: Optional[Path] = None) -> Optional[int]:
        proc = self._processes.get(pid)
        if proc is not None:
            return proc.poll()
        return None

    def kill(self, pid: int, server_name: Optional[str] = None,
             work_dir: Optional[Path] = None) -> None:
        proc = self._processes.get(pid)
        try:
            if proc is not None:
                proc.terminate()
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
