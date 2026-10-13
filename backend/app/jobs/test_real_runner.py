import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.jobs.real_runner import RealRunner


class RealRunnerTest(unittest.TestCase):
    def setUp(self):
        self.server = {"name": "local", "host": "127.0.0.1"}
        self.runner = RealRunner()

    def test_local_success_and_exit_code(self):
        with tempfile.TemporaryDirectory() as folder, patch("app.jobs.real_runner.settings.get_servers", return_value=[self.server]):
            command = 'sh -c "echo ready"' if os.name == "posix" else 'cmd /c "echo ready"'
            pid = self.runner.start("job", 1, Path(folder), command, "local")
            for _ in range(50):
                if not self.runner.is_alive(pid, "local", Path(folder)):
                    break
                time.sleep(.02)
            self.assertEqual(self.runner.get_return_code(pid, "local", Path(folder)), 0)
            self.assertIn("ready", (Path(folder) / "workflow.log").read_text())

    def test_local_kill(self):
        with tempfile.TemporaryDirectory() as folder, patch("app.jobs.real_runner.settings.get_servers", return_value=[self.server]):
            command = "sleep 20" if os.name == "posix" else "timeout /t 20 /nobreak"
            pid = self.runner.start("job", 1, Path(folder), command, "local")
            self.runner.kill(pid, "local", Path(folder))
            for _ in range(50):
                if not self.runner.is_alive(pid, "local", Path(folder)):
                    break
                time.sleep(.02)
            self.assertFalse(self.runner.is_alive(pid, "local", Path(folder)))

    def test_local_kill_escalates_to_sigkill_if_process_ignores_sigterm(self):
        """
        重現真實 bug 場景：process 收到 SIGTERM 沒有真的結束（就像 STAR-CCM+
        的 MPI launcher 有時候沒辦法把訊號乾淨轉發給所有 rank process）。
        用一個會自己註冊 handler 忽略 SIGTERM 的 Python 子行程模擬「刻意不理會
        SIGTERM」，驗證 grace period 過後真的會被升級成 SIGKILL 強制終止，
        而不是永遠留著吃 CPU（這個場景已經用 os.killpg 手動實測過確認行為）。
        """
        with tempfile.TemporaryDirectory() as folder, patch("app.jobs.real_runner.settings.get_servers", return_value=[self.server]):
            command = (
                f"{sys.executable} -c "
                "\"import signal,time; signal.signal(signal.SIGTERM, lambda *a: None); time.sleep(30)\""
            )
            pid = self.runner.start("job", 1, Path(folder), command, "local")
            time.sleep(0.5)  # 確認子行程真的先跑起來、註冊好 handler 了
            self.assertTrue(self.runner._process_group_alive(True, self.server, pid))

            self.runner.kill(pid, "local", Path(folder), grace_sec=0.6)

            # 剛送出 SIGTERM 那瞬間：外層 wrapper/shell 會死，但忽略訊號的
            # 孫行程還活著（且 process group 沿用同一個 pgid），group 應該還活著
            time.sleep(0.2)
            self.assertTrue(
                self.runner._process_group_alive(True, self.server, pid),
                "子行程設定了忽略 SIGTERM，這個時間點 process group 應該還活著",
            )

            # 等超過 grace_sec，背景執行緒應該已經升級成 SIGKILL，group 真的死了
            deadline = time.time() + 5
            while time.time() < deadline:
                if not self.runner._process_group_alive(True, self.server, pid):
                    break
                time.sleep(0.1)
            self.assertFalse(
                self.runner._process_group_alive(True, self.server, pid),
                "grace period 過後應該被升級成 SIGKILL 強制終止（SIGKILL 沒辦法被忽略）",
            )


class RemotePathMismatchTest(unittest.TestCase):
    """
    重現真實 bug：host 機器（跑 Web Service）跟目標 server（遠端）掛載同一份
    NFS 在不同的本機路徑（例如 host 是 /home/usera/CFD_cloud_NFS，
    目標是 /home/userb/CFD_cloud_NFS）。

    這裡故意讓 work_dir 指向一個「host 機器上肯定不存在」的路徑
    （/nonexistent_on_host/...），只有透過 mock 的 SSH cat 才「看得到」內容
    —— 如果程式碼不小心又改回用本機 Path.read_text() 去讀，這個測試會直接
    因為 FileNotFoundError / RuntimeError 失敗，而不是被本機檔案系統
    「不小心」矇混過關。
    """

    def setUp(self):
        self.server = {
            "name": "RemoteMachine", "host": "192.0.2.10", "ssh_user": "userb",
            "ssh_port": 22, "ssh_key_path": "~/.ssh/id_ed25519",
        }
        self.runner = RealRunner(ssh_timeout=5)
        # work_dir 用一個 host 機器上保證不存在的路徑，
        # 確保測試不會不小心因為本機剛好有這個路徑而矇混過關
        self.remote_work_dir = Path("/nonexistent_on_host/CFD_cloud_NFS/Straight/TH12_TEST")

    def _fake_ssh(self, server, remote_command, *, check=False):
        """模擬遠端主機的行為，不實際發出任何網路連線。"""
        if remote_command.startswith("cd "):
            # start() 一開始的 nohup 啟動指令
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "run.pid" in remote_command:
            return SimpleNamespace(returncode=0, stdout="88888\n", stderr="")
        if self.runner.EXIT_FILE in remote_command:
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="no such file")

    def test_start_reads_pid_via_ssh_not_local_filesystem(self):
        with patch("app.jobs.real_runner.settings.get_servers", return_value=[self.server]), \
             patch.object(RealRunner, "_ssh", side_effect=self._fake_ssh):
            pid = self.runner.start(
                "job", 31, self.remote_work_dir,
                command="/home/userb/star/bin/starccm+ -np 31 -batch gui_run.java case.sim",
                server_name="RemoteMachine",
            )
            self.assertEqual(pid, 88888, "應該要能透過 SSH cat 讀到 PID，不是嘗試讀本機路徑")

    def test_get_return_code_reads_exit_file_via_ssh_not_local_filesystem(self):
        with patch("app.jobs.real_runner.settings.get_servers", return_value=[self.server]), \
             patch.object(RealRunner, "_ssh", side_effect=self._fake_ssh):
            code = self.runner.get_return_code(88888, server_name="RemoteMachine", work_dir=self.remote_work_dir)
            self.assertEqual(code, 0, "應該要能透過 SSH cat 讀到 exit code，不是預設回傳 1（誤判成失敗）")

    def test_get_return_code_falls_back_to_1_when_ssh_cannot_read_file(self):
        """如果遠端真的讀不到（例如檔案還沒寫出來），要保守回傳 1，不能亂猜成功。"""
        def ssh_returns_nothing(server, remote_command, *, check=False):
            return SimpleNamespace(returncode=1, stdout="", stderr="No such file or directory")

        with patch("app.jobs.real_runner.settings.get_servers", return_value=[self.server]), \
             patch.object(RealRunner, "_ssh", side_effect=ssh_returns_nothing):
            code = self.runner.get_return_code(88888, server_name="RemoteMachine", work_dir=self.remote_work_dir)
            self.assertEqual(code, 1)

    def test_remote_kill_escalates_to_sigkill_when_group_survives_term(self):
        """
        重現真實 bug：SSH kill -TERM 送出後，process group 依然存活
        （對照真實 log：STAR-CCM+ 的 MPI launcher 有轉發訊號，但沒有讓所有
        process 真的結束，CPU 持續被佔用直到手動 pkill）。驗證 grace period
        過後，會自動再送一次 SIGKILL，不需要人工介入。
        """
        calls = []

        def fake_ssh(server, remote_command, *, check=False):
            calls.append(remote_command)
            if remote_command.startswith("kill -0"):
                # 模擬 process group 收到 TERM 後仍然存活
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("app.jobs.real_runner.settings.get_servers", return_value=[self.server]), \
             patch.object(RealRunner, "_ssh", side_effect=fake_ssh):
            self.runner.kill(99999, server_name="RemoteMachine", work_dir=Path("/remote/x"), grace_sec=0.4)
            # kill() 本身立刻回傳（實際升級邏輯在背景執行緒），等它跑完
            time.sleep(1.2)

        term_calls = [c for c in calls if c.startswith("kill -TERM")]
        kill_calls = [c for c in calls if c.startswith("kill -KILL")]
        self.assertTrue(term_calls, "應該先送過 SIGTERM")
        self.assertTrue(kill_calls, "process group 持續存活，grace period 過後應該自動升級成 SIGKILL")

    def test_remote_kill_does_not_escalate_when_term_succeeds(self):
        """process group 真的被 SIGTERM 結束的正常情況，不該多送一次 SIGKILL。"""
        calls = []

        def fake_ssh(server, remote_command, *, check=False):
            calls.append(remote_command)
            if remote_command.startswith("kill -0"):
                # process group 已經不存在了
                return SimpleNamespace(returncode=1, stdout="", stderr="No such process")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("app.jobs.real_runner.settings.get_servers", return_value=[self.server]), \
             patch.object(RealRunner, "_ssh", side_effect=fake_ssh):
            self.runner.kill(99999, server_name="RemoteMachine", work_dir=Path("/remote/x"), grace_sec=0.4)
            time.sleep(1.2)

        kill_calls = [c for c in calls if c.startswith("kill -KILL")]
        self.assertFalse(kill_calls, "SIGTERM 已經成功結束，不應該再多送一次 SIGKILL")


if __name__ == "__main__":
    unittest.main()
