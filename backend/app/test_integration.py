"""
端對端整合測試：透過真正的 HTTP API（不是直接呼叫 Python 物件）
模擬「登入 → 新增 Case → 排程執行 → 完成」的完整流程。

跟 test_api.py 的差別：test_api.py 只驗證路由能連通（回 200），
這支測試會真的建立一個 case、真的透過 RealRunner 啟動一個 process、
真的等它跑完、確認狀態正確變成 DONE，驗證 workflow.py + queue_manager.py +
real_runner.py + routers 全部接在一起真的能動，而不只是個別測試都過。

跑法：
    cd backend
    python3 -m app.test_integration
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import yaml


class IntegrationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)

        # 1. 複製一份乾淨的 data/ 到暫存目錄，指向暫存 NFS
        from app.test_support import prepare_data
        data = prepare_data(root)

        nfs_root = root / "nfs"

        # 2. 建立一支「假的 starccm+」：睡 1 秒後正常結束（exit 0），
        #    這樣可以驗證正常 DONE 的完整路徑（不是只測失敗）
        fake_starccm = root / "fake_starccm.sh"
        fake_starccm.write_text(
            "#!/bin/bash\necho \"fake starccm+ running with args: $@\"\nsleep 1\nexit 0\n",
            encoding="utf-8",
        )
        fake_starccm.chmod(0o755)

        servers = yaml.safe_load((data / "servers.yaml").read_text(encoding="utf-8"))
        servers["servers"] = [servers["servers"][0]]
        servers["servers"][0]["name"] = "TestServer"
        servers["servers"][0]["host"] = "127.0.0.1"
        servers["servers"][0]["nfs_path"] = str(nfs_root)
        servers["servers"][0]["starccm_path"] = str(fake_starccm)
        servers["servers"][0]["total_cores"] = 8
        servers["servers"][0]["reserved_cores"] = 0
        (data / "servers.yaml").write_text(yaml.safe_dump(servers, allow_unicode=True, sort_keys=False), encoding="utf-8")

        app_settings = yaml.safe_load((data / "app_settings.yaml").read_text(encoding="utf-8"))
        app_settings["nfs_root"] = str(nfs_root)
        app_settings["viewer_data_path"] = str(nfs_root)
        app_settings["status_poll_interval_sec"] = 1  # 加快測試速度
        (data / "app_settings.yaml").write_text(yaml.safe_dump(app_settings, allow_unicode=True, sort_keys=False), encoding="utf-8")

        # 3. 建立假的 template 資料夾（模擬 NFS 上的 template_CFD）
        template_name = app_settings["template_folder_name"]
        for sim_type_folder in app_settings["simulation_type_folders"].values():
            template = nfs_root / sim_type_folder / template_name
            template.mkdir(parents=True)
            (template / f"{template_name}.sim").write_text("FAKE_SIM")
            (template / "body.x_t").write_text("FAKE_BODY")
            (template / "unsprung.x_t").write_text("FAKE_UNSPRUNG")
            (template / app_settings["entry_macro"]).write_text("FAKE_MACRO")

        # 3b. 加入 user / visitor 測試帳號，驗證三級權限
        users = yaml.safe_load((data / "users.yaml").read_text(encoding="utf-8"))
        from app.auth import hash_password
        users["users"].append({"name": "TestUser", "role": "user", "password": hash_password("testpass")})
        users["users"].append({"name": "TestVisitor", "role": "visitor", "password": hash_password("testpass")})
        (data / "users.yaml").write_text(yaml.safe_dump(users, allow_unicode=True, sort_keys=False), encoding="utf-8")

        os.environ["CFD_PLATFORM_DATA_DIR"] = str(data)

        from fastapi.testclient import TestClient
        from app.main import app
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.temp.cleanup()

    def test_full_job_session_flow(self):
        c = self.client

        # --- 登入 ---
        r = c.post("/api/login", json={"name": "admin", "password": "changeme"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["role"], "admin")

        # --- 查 server 狀態 ---
        r = c.get("/api/servers")
        self.assertEqual(r.status_code, 200)
        servers = r.json()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["available_cores"], 8)
        print(f"  Server 初始狀態: available={servers[0]['available_cores']}/{servers[0]['total_cores']}")

        # --- 新增 Case（模擬前端表單送出）---
        r = c.post(
            "/api/jobs",
            data={
                "case_name": "IT_TEST_001",
                "simulation_type": "1",
                "server_name": "TestServer",
                "mode": "half",
                "parameters": '{"simulation_type": "1", "free_stream_velocity": "20.0"}',
                "force": "false",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()
        print(f"  Job 建立: {job['case_name']}, status={job['status']}, cores={job['cores_allocated']}")
        self.assertEqual(job["status"], "SOLVING", "8 顆核心、half 模式應該立刻開始")
        self.assertEqual(job["cores_allocated"], 4, "8//2 應該是 4")

        # --- 確認資料夾真的被建立、檔案都在 ---
        case_folder = Path(job["work_dir"]) if "work_dir" in job else None
        # work_dir 可能沒有序列化出來，直接用已知路徑找
        expected_folder = Path(os.environ["CFD_PLATFORM_DATA_DIR"]).parent / "nfs" / "Straight" / "IT_TEST_001"
        self.assertTrue(expected_folder.exists(), f"Case 資料夾應該被建立: {expected_folder}")
        self.assertTrue((expected_folder / "IT_TEST_001.sim").exists())
        self.assertTrue((expected_folder / "case_parameter.dat").exists())
        params = (expected_folder / "case_parameter.dat").read_text()
        self.assertIn("free_stream_velocity = 20.0", params)
        print(f"  Case 資料夾內容驗證通過: {expected_folder}")

        # --- 確認 server 的 available_cores 減少了 ---
        r = c.get("/api/servers")
        self.assertEqual(r.json()[0]["available_cores"], 4, "用掉 4 顆後應該剩 4 顆")

        # --- 覆蓋保護：SOLVING 中不能建同名 case ---
        r = c.post(
            "/api/jobs",
            data={
                "case_name": "IT_TEST_001", "simulation_type": "1", "server_name": "TestServer",
                "mode": "half", "parameters": "{}", "force": "false",
            },
        )
        self.assertEqual(r.status_code, 409, "SOLVING 中應該被擋")
        print(f"  覆蓋保護正確擋下: {r.json()['detail']}")

        # --- 等 job 真的跑完（fake starccm+ 睡 1 秒），確認背景 monitor 有在動 ---
        print("  等待背景 JobMonitor 偵測到 process 結束...")
        deadline = time.time() + 15
        final_status = None
        while time.time() < deadline:
            r = c.get("/api/jobs")
            jobs = r.json()
            final_status = next(j for j in jobs if j["id"] == job["id"])["status"]
            if final_status != "SOLVING":
                break
            time.sleep(0.5)

        self.assertEqual(final_status, "DONE", f"應該正常結束為 DONE，實際是 {final_status}")
        print(f"  ✅ Job 自動完成，狀態正確變成 DONE")

        # --- 確認核心被釋放 ---
        r = c.get("/api/servers")
        self.assertEqual(r.json()[0]["available_cores"], 8, "job 結束後應該全部釋放")
        print(f"  ✅ 核心正確釋放，回到 8/8 available")

        # --- 現在 DONE 了，應該可以覆蓋 ---
        r = c.post(
            "/api/jobs",
            data={
                "case_name": "IT_TEST_001", "simulation_type": "1", "server_name": "TestServer",
                "mode": "all", "parameters": "{}", "force": "true",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        print(f"  ✅ DONE 之後允許 force 覆蓋，重新建立成功: status={r.json()['status']}, cores={r.json()['cores_allocated']}")

        # --- Kill 這個新跑起來的 job（測 admin kill 功能）---
        new_job_id = r.json()["id"]
        r = c.delete(f"/api/jobs/{new_job_id}")
        self.assertEqual(r.status_code, 200, r.text)
        print("  ✅ Admin kill 成功送出")

        deadline = time.time() + 15
        while time.time() < deadline:
            r = c.get("/api/jobs")
            jobs = r.json()
            status = next(j for j in jobs if j["id"] == new_job_id)["status"]
            if status != "SOLVING":
                break
            time.sleep(0.5)
        self.assertEqual(status, "FAILED", "被 kill 的 job 應該變成 FAILED")
        print(f"  ✅ 被 kill 的 job 正確變成 FAILED")

    def test_job_purge_and_clear_failed(self):
        """
        獨立、自成一體的測試（不依賴其他測試留下的狀態）：驗證 admin
        能刪除單筆 job 紀錄、能一鍵清除所有 FAILED，並且驗證路由順序
        真的修對了 —— DELETE /api/jobs/failed 不會被 DELETE /api/jobs/{job_id}
        攔截（如果順序寫錯，"failed" 會被當成 job_id 傳進 kill_job，
        導致回傳 409 "Job failed 不存在" 而不是真的執行清除）。
        """
        c = self.client
        c.post("/api/login", json={"name": "admin", "password": "changeme"})

        def make_failed_job(case_name: str) -> str:
            r = c.post(
                "/api/jobs",
                data={
                    "case_name": case_name, "simulation_type": "1", "server_name": "TestServer",
                    "mode": "all", "parameters": "{}", "force": "true",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            job_id = r.json()["id"]
            r = c.delete(f"/api/jobs/{job_id}")
            self.assertEqual(r.status_code, 200, r.text)
            deadline = time.time() + 15
            status = None
            while time.time() < deadline:
                jobs = c.get("/api/jobs").json()
                status = next(j for j in jobs if j["id"] == job_id)["status"]
                if status != "SOLVING":
                    break
                time.sleep(0.3)
            self.assertEqual(status, "FAILED")
            return job_id

        fail_a = make_failed_job("PURGE_TEST_A")
        fail_b = make_failed_job("PURGE_TEST_B")
        print(f"  建立了兩筆 FAILED job 用於測試: {fail_a}, {fail_b}")

        # --- 個別刪除一筆 ---
        r = c.delete(f"/api/jobs/{fail_a}/record")
        self.assertEqual(r.status_code, 200, r.text)
        jobs = c.get("/api/jobs").json()
        self.assertNotIn(fail_a, [j["id"] for j in jobs], "刪除後不應該還出現在列表裡")
        self.assertIn(fail_b, [j["id"] for j in jobs], "另一筆不該被誤刪")
        print("  ✅ 個別刪除單筆 job 紀錄成功")

        # --- 驗證路由順序：DELETE /api/jobs/failed 要真的命中 clear_failed，
        #     不能被 /{job_id} 攔截誤判成「刪除 id=failed 的 job」---
        r = c.delete("/api/jobs/failed")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("removed", r.json(), "回應格式應該是 clear_failed 的格式，不是 kill_job 的 {'ok': true}")
        self.assertGreaterEqual(r.json()["removed"], 1)
        print(f"  ✅ 路由順序正確：/failed 沒有被 /{{job_id}} 攔截，清除了 {r.json()['removed']} 筆")

        jobs = c.get("/api/jobs").json()
        self.assertNotIn(fail_b, [j["id"] for j in jobs], "clear_failed 後應該也被清掉")

        # --- 不能刪除還在 SOLVING 的 job ---
        r = c.post(
            "/api/jobs",
            data={
                "case_name": "PURGE_TEST_RUNNING", "simulation_type": "1", "server_name": "TestServer",
                "mode": "all", "parameters": "{}", "force": "true",
            },
        )
        running_id = r.json()["id"]
        self.assertEqual(r.json()["status"], "SOLVING")
        r = c.delete(f"/api/jobs/{running_id}/record")
        self.assertEqual(r.status_code, 409, "不應該能刪除還在跑的 job 紀錄")
        print("  ✅ 正確拒絕刪除 SOLVING 中的 job 紀錄")
        c.delete(f"/api/jobs/{running_id}")  # 收尾：把它 kill 掉，不留殘留 process

    def test_job_simulation_type_and_note(self):
        """驗證新增的 simulation_type / note 欄位能正確存入、讀出、事後編輯。"""
        c = self.client
        c.post("/api/login", json={"name": "admin", "password": "changeme"})

        r = c.post(
            "/api/jobs",
            data={
                "case_name": "NOTE_TEST_001", "simulation_type": "2", "server_name": "TestServer",
                "mode": "all", "parameters": "{}", "force": "true",
                "note": "Testing yaw sweep at 5 degrees",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()
        self.assertEqual(job["simulation_type"], 2)
        self.assertEqual(job["note"], "Testing yaw sweep at 5 degrees")
        print(f"  ✅ 新增 job 時 simulation_type/note 正確存入: {job['simulation_type']}, {job['note']}")

        job_id = job["id"]
        r = c.put(f"/api/jobs/{job_id}/note", json={"note": "Updated note after review"})
        self.assertEqual(r.status_code, 200, r.text)

        jobs = c.get("/api/jobs").json()
        updated = next(j for j in jobs if j["id"] == job_id)
        self.assertEqual(updated["note"], "Updated note after review")
        print(f"  ✅ 事後編輯 note 成功: {updated['note']}")

        c.delete(f"/api/jobs/{job_id}")  # 收尾

    def test_three_tier_permissions(self):
        """
        驗證三級權限：
          - visitor：可以看 job 列表，但不能新增 case、不能編輯 note、
            不能看 servers.yaml 設定內容
          - user：可以新增 case、可以編輯 note，但不能看 servers.yaml
          - admin：全部都能做
        """
        c = self.client

        # --- visitor ---
        r = c.post("/api/login", json={"name": "TestVisitor", "password": "testpass"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["role"], "visitor")

        r = c.get("/api/jobs")
        self.assertEqual(r.status_code, 200, "visitor 應該能看 job 列表")

        r = c.post(
            "/api/jobs",
            data={
                "case_name": "VISITOR_SHOULD_FAIL", "simulation_type": "1", "server_name": "TestServer",
                "mode": "half", "parameters": "{}", "force": "false", "note": "",
            },
        )
        self.assertEqual(r.status_code, 403, "visitor 不應該能新增 case")
        print(f"  ✅ visitor 被正確擋下新增 case: {r.json()['detail']}")

        r = c.get("/api/settings/servers")
        self.assertEqual(r.status_code, 403, "visitor 不應該能看 servers 設定")
        print("  ✅ visitor 被正確擋下查看 servers 設定")

        # --- user ---
        r = c.post("/api/login", json={"name": "TestUser", "password": "testpass"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["role"], "user")

        r = c.post(
            "/api/jobs",
            data={
                "case_name": "USER_CAN_CREATE", "simulation_type": "1", "server_name": "TestServer",
                "mode": "half", "parameters": "{}", "force": "false", "note": "user role test",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        job_id = r.json()["id"]
        print("  ✅ user 可以新增 case")

        r = c.put(f"/api/jobs/{job_id}/note", json={"note": "edited by user"})
        self.assertEqual(r.status_code, 200, "user 應該能編輯 note")
        print("  ✅ user 可以編輯 note")

        r = c.get("/api/settings/servers")
        self.assertEqual(r.status_code, 403, "user 一樣不該能看 servers 設定（只有 admin 能）")
        print("  ✅ user 被正確擋下查看 servers 設定")

        # 收尾要切回 admin 才能 kill（順便驗證：user 自己也不能 kill 自己建的 job）
        r = c.delete(f"/api/jobs/{job_id}")
        self.assertEqual(r.status_code, 403, "user 不應該能 kill job（只有 admin 能）")
        print("  ✅ user 被正確擋下 kill job")
        c.post("/api/login", json={"name": "admin", "password": "changeme"})
        c.delete(f"/api/jobs/{job_id}")

        # --- admin ---
        c.post("/api/login", json={"name": "admin", "password": "changeme"})
        r = c.get("/api/settings/servers")
        self.assertEqual(r.status_code, 200, "admin 應該能看 servers 設定")
        print("  ✅ admin 可以查看 servers 設定")


if __name__ == "__main__":
    unittest.main(verbosity=2)
