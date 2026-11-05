import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from . import viewer
from ..dependencies import current_user


class ViewerIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        for folder, value in (("Straight", "1"), ("Cornering", "2")):
            case = self.root / folder / "TH12_001"
            (case / "slice").mkdir(parents=True)
            (case / "Result.dat").write_text(f"mean_Cla = {value}\n")
            (case / "case_parameter.dat").write_text(f"marker = {value}\n")
            (case / "slice" / "0.png").write_bytes(value.encode())
            (case / "plot.csv").write_text(f"x,y\n0,{value}\n")
            (case / "q-criterion.stl").write_bytes(value.encode())
        (self.root / "Yaw" / "unique").mkdir(parents=True)
        self.root_patch = patch.object(viewer, "_root", return_value=self.root)
        self.settings_patch = patch.object(viewer.settings, "get_app_settings", return_value={
            "simulation_type_folders": {"1": "Straight", "2": "Yaw", "3": "Cornering"}
        })
        self.root_patch.start()
        self.settings_patch.start()
        app = FastAPI()
        app.include_router(viewer.router)
        app.dependency_overrides[current_user] = lambda: object()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.settings_patch.stop()
        self.root_patch.stop()
        self.tmp.cleanup()

    def get(self, folder, name, suffix):
        identity = "@case:" + json.dumps([folder, name], separators=(",", ":"))
        return self.client.get("/api/viewer/case/" + quote(identity, safe="") + suffix)

    def test_same_name_all_data_sources(self):
        for folder, value in (("Straight", "1"), ("Cornering", "2")):
            self.assertEqual(self.get(folder, "TH12_001", "/result").json()["mean_Cla"], value)
            self.assertEqual(self.get(folder, "TH12_001", "/parameters").json()["marker"], value)
            self.assertEqual(self.get(folder, "TH12_001", "/slice_types").json(), ["slice"])
            self.assertEqual(self.get(folder, "TH12_001", "/slices/slice").json(), ["0.png"])
            self.assertEqual(self.get(folder, "TH12_001", "/slice/slice/0").content, value.encode())
            self.assertEqual(self.get(folder, "TH12_001", "/csv").json(), ["plot.csv"])
            self.assertEqual(self.get(folder, "TH12_001", "/csv/plot.csv").json()["rows"][0][1], float(value))
            self.assertEqual(self.get(folder, "TH12_001", "/stl").content, value.encode())

    def test_legacy_and_invalid_identity(self):
        self.assertEqual(self.client.get('/api/viewer/case/unique/slice_types').status_code, 200)
        self.assertEqual(self.get('Yaw', 'unique', '/slice_types').status_code, 200)
        self.assertEqual(self.get('Unknown', 'unique', '/slice_types').status_code, 400)
        self.assertEqual(self.get('Yaw', '..', '/slice_types').status_code, 400)
        self.assertEqual(self.get('Cornering', 'unique', '/slice_types').status_code, 404)


if __name__ == '__main__':
    unittest.main()
