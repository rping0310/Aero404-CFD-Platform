"""Production job runner for local processes and SSH/nohup workers."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .runner import JobRunner
from ..config import settings


class RealRunner(JobRunner):
    EXIT_FILE = ".aero404.exitcode"

    def __init__(self, ssh_timeout: int = 15):
        self.ssh_timeout = ssh_timeout
        self._processes: dict[int, subprocess.Popen] = {}

    def _server(self, name: Optional[str]) -> dict:
        for server in settings.get_servers():
            if server.get("name") == name:
                return server
        raise ValueError(f"Server '{name}' not found")

    @staticmethod
    def _is_local(server: dict) -> bool:
        return str(server.get("host", "")).lower() in {"127.0.0.1", "localhost", "::1"}

    def _ssh_args(self, server: dict) -> list[str]:
        args = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.ssh_timeout}"]
        key = os.path.expanduser(str(server.get("ssh_key_path", "")))
        if key:
            args.extend(["-i", key])
        args.extend(["-p", str(server.get("ssh_port", 22))])
        args.append(f"{server['ssh_user']}@{server['host']}")
        return args

    def _ssh(self, server: dict, remote_command: str, *, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*self._ssh_args(server), remote_command], capture_output=True, text=True,
            timeout=self.ssh_timeout, check=check,
        )

    def _read_remote_file(self, server: dict, path) -> Optional[str]:
        """
        用 SSH cat 讀遠端主機上的檔案內容。

        不能用本機的 Path.read_text() 去讀 —— work_dir 存的是「目標主機自己
        看到的路徑」（例如 /home/userb/CFD_cloud_NFS/...），就算底層是同一份
        共用 NFS，本機（跑這個 Web Service 的機器）通常掛載在不同的路徑
        （例如 /home/usera/CFD_cloud_NFS/...），本機端直接讀那個路徑字串
        幾乎必定會是 FileNotFoundError。一律透過 SSH 在目標主機上執行 cat，
        才是唯一可靠的讀法。
        """
        try:
            result = self._ssh(server, f"cat {shlex.quote(str(path))} 2>/dev/null")
        except (subprocess.SubprocessError, OSError):
            return None
        if result.returncode != 0:
            return None
        content = result.stdout.strip()
        return content if content else None

    def start(self, job_id: str, cores: int, work_dir: Path, command: Optional[str] = None,
              server_name: Optional[str] = None) -> int:
        if not command:
            raise ValueError("RealRunner requires a command")
        server = self._server(server_name)
        work_dir = Path(work_dir)
        if self._is_local(server):
            work_dir.mkdir(parents=True, exist_ok=True)
            exit_file = work_dir / self.EXIT_FILE
            exit_file.unlink(missing_ok=True)
            log = (work_dir / "workflow.log").open("a", encoding="utf-8")
            wrapper = (
                "import pathlib,subprocess,sys; "
                "code=subprocess.call(sys.argv[1],shell=True); "
                "pathlib.Path(sys.argv[2]).write_text(str(code),encoding='utf-8'); "
                "raise SystemExit(code)"
            )
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-c", wrapper, command, str(exit_file)],
                    cwd=str(work_dir), stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                log.close()
            self._processes[proc.pid] = proc
            (work_dir / "run.pid").write_text(str(proc.pid), encoding="utf-8")
            return proc.pid

        quoted_dir = shlex.quote(str(work_dir))
        exit_path = shlex.quote(str(work_dir / self.EXIT_FILE))
        wrapped = f"{command}; code=$?; printf '%s' \"$code\" > {exit_path}; exit $code"
        remote = (
            f"cd {quoted_dir} && rm -f {exit_path} && {{ "
            f"nohup setsid sh -c {shlex.quote(wrapped)} </dev/null >> workflow.log 2>&1 & "
            f"echo $! > run.pid; }}"
        )
        self._ssh(server, remote, check=True)
        pid_path = work_dir / "run.pid"
        for _ in range(25):
            content = self._read_remote_file(server, pid_path)
            if content:
                try:
                    return int(content)
                except ValueError:
                    pass
            time.sleep(0.2)
        raise RuntimeError(f"Could not read PID after remote start: {pid_path}")

    def is_alive(self, pid: int, server_name: Optional[str] = None,
                 work_dir: Optional[Path] = None) -> bool:
        server = self._server(server_name)
        if self._is_local(server):
            proc = self._processes.get(pid)
            if proc is not None:
                return proc.poll() is None
            try:
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, PermissionError, OSError):
                return False
        try:
            result = self._ssh(server, f"kill -0 {int(pid)} 2>/dev/null")
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            # A network failure is not proof that STAR-CCM+ exited.
            return True

    def get_return_code(self, pid: int, server_name: Optional[str] = None,
                        work_dir: Optional[Path] = None) -> Optional[int]:
        server = self._server(server_name)
        proc = self._processes.get(pid)
        if self._is_local(server):
            if proc is not None:
                return proc.poll()
            if work_dir:
                path = Path(work_dir) / self.EXIT_FILE
                try:
                    return int(path.read_text(encoding="utf-8").strip())
                except (FileNotFoundError, ValueError):
                    pass
            return 1

        # 遠端：跟 start() 的 PID 讀取同一個道理，不能用本機路徑讀 exit code 檔，
        # 一律透過 SSH cat 讀目標主機上的檔案。這個如果沒修，遠端 job 就算真的
        # 成功跑完，也會因為讀不到檔案、預設回傳 1，被永遠誤判成 FAILED。
        if work_dir:
            content = self._read_remote_file(server, Path(work_dir) / self.EXIT_FILE)
            if content is not None:
                try:
                    return int(content)
                except ValueError:
                    pass
        return 1

    # ---- LOGKILL_PATCH ----
    def find_in_log(self, patterns: list[str], server_name: Optional[str] = None,
                    work_dir: Optional[Path] = None) -> Optional[str]:
        """
        Fixed-string search (grep -F) in work_dir/workflow.log on the target host.
        Returns the first pattern that matched, or None (no match / unreachable).
        """
        if not patterns or not work_dir:
            return None
        server = self._server(server_name)
        log_path = Path(work_dir) / "workflow.log"
        try:
            if self._is_local(server):
                cmd = ["grep", "-m1", "-o", "-F"]
                for pat in patterns:
                    cmd += ["-e", pat]
                cmd.append(str(log_path))
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            else:
                remote = "grep -m1 -o -F " + " ".join(f"-e {shlex.quote(p)}" for p in patterns) \
                    + f" {shlex.quote(str(log_path))} 2>/dev/null"
                result = self._ssh(server, remote)
        except (subprocess.SubprocessError, OSError):
            return None
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().splitlines()
        return lines[0] if lines else None

    def tail_file(self, server_name: Optional[str], path, lines: int = 300) -> Optional[str]:
        """Last `lines` lines of a file on the target host; None if missing / unreachable."""
        lines = max(1, int(lines))
        server = self._server(server_name)
        if self._is_local(server):
            try:
                with open(path, "rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - (lines * 400 + 4096)))
                    data = handle.read()
            except OSError:
                return None
            return "\n".join(data.decode("utf-8", errors="replace").splitlines()[-lines:])
        try:
            result = self._ssh(server, f"tail -n {lines} {shlex.quote(str(path))} 2>/dev/null")
        except (subprocess.SubprocessError, OSError):
            return None
        return result.stdout if result.returncode == 0 else None
    @staticmethod
    def _page_bounds(total: int, lines: int, page: int):
        """page <= 0 (or too large) -> last page. Returns (page, pages, start, end), 1-based inclusive."""
        pages = max(1, -(-total // lines))
        if page <= 0 or page > pages:
            page = pages
        return page, pages, (page - 1) * lines + 1, min(total, page * lines)

    def read_log_page(self, server_name: Optional[str], path, lines: int = 1000, page: int = 0):
        """One page of `lines` lines. Returns (text, page, pages, total) or None if missing / unreachable."""
        lines = max(1, int(lines))
        server = self._server(server_name)
        if self._is_local(server):
            try:
                with open(path, "rb") as handle:
                    rows = handle.read().decode("utf-8", errors="replace").splitlines()
            except OSError:
                return None
            cur, pages, start, end = self._page_bounds(len(rows), lines, int(page))
            return "\n".join(rows[start - 1:end]), cur, pages, len(rows)
        try:
            q = shlex.quote(str(path))
            count = self._ssh(server, f"awk 'END{{print NR}}' {q} 2>/dev/null")
            if count.returncode != 0:
                return None
            total = int(count.stdout.strip() or 0)
            cur, pages, start, end = self._page_bounds(total, lines, int(page))
            if total == 0:
                return "", 1, 1, 0
            result = self._ssh(server, f"sed -n '{start},{end}p;{end}q' {q} 2>/dev/null")
        except (subprocess.SubprocessError, OSError, ValueError):
            return None
        return (result.stdout.rstrip("\n"), cur, pages, total) if result.returncode == 0 else None
    # ---- /LOGKILL_PATCH ----

    def kill(self, pid: int, server_name: Optional[str] = None,
             work_dir: Optional[Path] = None, grace_sec: float = 6.0) -> None:
        """
        先送 SIGTERM 讓 starccm+ 有機會優雅收尾，但不能只送一次就假設它一定會
        乾淨結束 —— 實際觀察到：MPI launcher 收到 SIGTERM 後會自己轉發訊號給
        旗下所有 rank process，但這個轉發鏈在 process 數量多時不保證全部成功，
        留下殘留 process 繼續佔用 CPU，直到手動 pkill 才會真的停。

        所以這裡在背景執行緒等一段緩衝時間後，再檢查一次「整個 process group」
        是否還有任何存活成員（不是只看原本那個 pid —— wrapper shell 可能已經
        結束，但它底下的子 process 可能還活著，process group 的存活狀態要看
        group 本身，不能只看 leader 那一個 pid），沒死乾淨的話直接升級成
        SIGKILL 強制終止。用背景執行緒是為了不讓 HTTP 請求（使用者按下 Kill
        按鈕）被這幾秒的等待卡住。
        """
        server = self._server(server_name)
        is_local = self._is_local(server)

        if is_local:
            self._send_signal_local(pid, signal.SIGTERM)
        else:
            self._send_signal_remote(server, pid, "TERM")

        def _escalate_if_still_alive() -> None:
            deadline = time.time() + grace_sec
            while time.time() < deadline:
                if not self._process_group_alive(is_local, server, pid):
                    return
                time.sleep(0.5)
            if self._process_group_alive(is_local, server, pid):
                if is_local:
                    self._send_signal_local(pid, signal.SIGKILL)
                else:
                    self._send_signal_remote(server, pid, "KILL")

        threading.Thread(target=_escalate_if_still_alive, daemon=True).start()

    def _process_group_alive(self, is_local: bool, server: dict, pgid: int) -> bool:
        """
        檢查整個 process group（不是單一 pid）是否還有任何存活成員。
        用負的 pid 對 kill 送訊號 0，這是標準的「檢查 process group」寫法：
        只要 group 裡還有任何一個成員活著就會成功，group 全部結束才會失敗。
        """
        if is_local:
            # 關鍵：如果我們自己手上還握著這個 pid 的 Popen 物件（本機啟動的
            # 都會存在 self._processes），而它已經結束了，這個 process 會變成
            # zombie 停在 process table 裡，直到有人呼叫 wait()/poll() 把它
            # reap 掉。zombie 依然是一個「存在」的 pid，killpg(pgid, 0) 對
            # zombie 一樣會回報成功，導致誤判成「還活著」，即使實際在跑的
            # 工作早就已經被清乾淨了。所以這裡先 poll() 一次讓它有機會被 reap，
            # 再去檢查 group 是否還有其他真正存活的成員。
            proc = self._processes.get(pgid)
            if proc is not None:
                proc.poll()
            try:
                os.killpg(pgid, 0)
                return True
            except ProcessLookupError:
                return False
            except (PermissionError, OSError):
                return True  # 沒辦法確認就保守假設還活著，避免漏殺
        try:
            result = self._ssh(server, f"kill -0 -- -{int(pgid)} 2>/dev/null")
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return True  # SSH 連不上不代表 process 真的死了，一樣保守處理

    def _send_signal_local(self, pid: int, sig: int) -> None:
        try:
            if os.name == "posix":
                os.killpg(pid, sig)
            else:
                proc = self._processes.get(pid)
                if proc is not None:
                    proc.kill() if sig == signal.SIGKILL else proc.terminate()
                else:
                    os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _send_signal_remote(self, server: dict, pid: int, sig_name: str) -> None:
        command = (
            f"kill -{sig_name} -- -{int(pid)} 2>/dev/null || "
            f"kill -{sig_name} {int(pid)} 2>/dev/null || true"
        )
        try:
            self._ssh(server, command)
        except (subprocess.SubprocessError, OSError):
            pass
