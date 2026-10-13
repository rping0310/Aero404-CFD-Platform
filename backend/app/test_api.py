import os
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml


class ApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        from app.test_support import prepare_data
        data = prepare_data(root)
        servers = yaml.safe_load((data / "servers.yaml").read_text(encoding="utf-8"))
        servers["servers"] = [servers["servers"][0]]
        servers["servers"][0]["nfs_path"] = str(root / "nfs")
        (data / "servers.yaml").write_text(yaml.safe_dump(servers, allow_unicode=True, sort_keys=False), encoding="utf-8")
        app_settings = yaml.safe_load((data / "app_settings.yaml").read_text(encoding="utf-8"))
        app_settings["nfs_root"] = str(root / "nfs")
        app_settings["viewer_data_path"] = str(root / "nfs")
        app_settings["status_poll_interval_sec"] = 1
        (data / "app_settings.yaml").write_text(yaml.safe_dump(app_settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.environ["CFD_PLATFORM_DATA_DIR"] = str(data)
        from fastapi.testclient import TestClient
        from app.main import app
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.temp.cleanup()

    def test_health_auth_and_core_routes(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        response = self.client.post("/api/login", json={"name": "admin", "password": "changeme"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/me").json()["role"], "admin")
        self.assertEqual(self.client.get("/api/jobs").status_code, 200)
        self.assertEqual(self.client.get("/api/servers").status_code, 200)
        self.assertEqual(self.client.get("/api/viewer/cases").json(), [])
        current = self.client.get("/api/settings").json()
        saved = self.client.put("/api/settings", json={"content": current["content"], "expected_mtime": current["mtime"]})
        self.assertEqual(saved.status_code, 200)


if __name__ == "__main__":
    unittest.main()
