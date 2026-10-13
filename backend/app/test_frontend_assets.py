"""
前端資產整合測試：模擬前端會呼叫的所有 viewer API，
用真實的假資料（Result.dat / slice PNG / CSV / STL）驗證回傳格式，
確保 app.js 裡假設的資料結構（columns/rows/log_scale、slices 陣列等）
跟後端實際回傳的一致。

跑法：
    cd backend
    python3 -m app.test_frontend_assets
"""

import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

import yaml


def _write_minimal_stl(path: Path) -> None:
    """寫一個最小的合法二進位 STL（1 個三角形），讓 three.js STLLoader 能 parse。"""
    header = b"aero404-test-stl".ljust(80, b"\0")
    tri_count = struct.pack("<I", 1)
    normal = struct.pack("<3f", 0, 0, 1)
    v1 = struct.pack("<3f", 0, 0, 0)
    v2 = struct.pack("<3f", 1, 0, 0)
    v3 = struct.pack("<3f", 0, 1, 0)
    attr = struct.pack("<H", 0)
    with path.open("wb") as f:
        f.write(header + tri_count + normal + v1 + v2 + v3 + attr)


def _write_minimal_png(path: Path) -> None:
    """1x1 透明 PNG，足夠讓前端的 <img> 顯示測試通過（不檢查畫面內容）。"""
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000a49444154789c6360000002000100ffff03000006"
        "0005570a4a0000000049454e44ae426082"
    )
    path.write_bytes(png_bytes)


class FrontendAssetsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        from app.test_support import prepare_data
        data = prepare_data(root)

        nfs_root = root / "nfs"

        servers = yaml.safe_load((data / "servers.yaml").read_text(encoding="utf-8"))
        servers["servers"] = [servers["servers"][0]]
        servers["servers"][0]["nfs_path"] = str(nfs_root)
        (data / "servers.yaml").write_text(yaml.safe_dump(servers, allow_unicode=True, sort_keys=False), encoding="utf-8")

        app_settings = yaml.safe_load((data / "app_settings.yaml").read_text(encoding="utf-8"))
        app_settings["nfs_root"] = str(nfs_root)
        app_settings["viewer_data_path"] = str(nfs_root)
        (data / "app_settings.yaml").write_text(yaml.safe_dump(app_settings, allow_unicode=True, sort_keys=False), encoding="utf-8")

        # 建立一個「已經跑完」的假 case，資料結構完全模擬 postprocess.java 的實際輸出
        case_folder = nfs_root / "Straight" / "TH12_DEMO"
        case_folder.mkdir(parents=True)

        (case_folder / "Result.dat").write_text(
            "mean_Cl/Cd = 2.4067\n"
            "mean_Cla = 1.7149\n"
            "mean_FW_DF = 50.6073\n"
            "mean_RW_DF = 41.7342\n"
            "mean_SW_DF = 48.4198\n"
            "mean_UT_DF = 3.8615\n"
            "mean_W_DF = 158.6284\n"
            "Aero_balance_x = 0.8027\n"
            "Aero_balance_z = 0.5889\n",
            encoding="utf-8",
        )

        (case_folder / "case_parameter.dat").write_text(
            "simulation_type = 1\n"
            "free_stream_velocity = 12.5\n"
            "pitch_angle = 0\n",
            encoding="utf-8",
        )

        # slice 資料夾（用真實 postprocess.java 的資料夾命名：帶空格）
        slice_dir = case_folder / "x direction velocity"
        slice_dir.mkdir()
        for i in range(3):
            _write_minimal_png(slice_dir / f"frame_{i:04d}.png")

        # 模擬使用者手動在 NFS 上新增的空資料夾（還沒有任何圖片）
        (case_folder / "z direction vorticity").mkdir()

        # Residuals CSV（應該被判定為 log scale）
        (case_folder / "Residuals_0000.000.csv").write_text(
            '"Iteration","Continuity: Residual","X-momentum: Residual"\n'
            "1.0,0.114,0.038\n2.0,0.092,0.031\n3.0,0.081,0.028\n",
            encoding="utf-8",
        )
        # Force CSV（應該被判定為 linear scale）
        (case_folder / "DF_0000.000.csv").write_text(
            '"mean_SW_DF Monitor: Iteration","mean_SW_DF Monitor: mean_SW_DF Monitor (N)"\n'
            "1.0,253.50\n2.0,254.10\n",
            encoding="utf-8",
        )

        _write_minimal_stl(case_folder / "q-criterion.stl")

        os.environ["CFD_PLATFORM_DATA_DIR"] = str(data)
        from fastapi.testclient import TestClient
        from app.main import app
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        cls.client.post("/api/login", json={"name": "admin", "password": "changeme"})

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.temp.cleanup()

    def test_index_and_static_assets_serve(self):
        c = self.client
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Aero404 CFD Platform", r.text)
        self.assertIn('x-data="platform()"', r.text)

        r = c.get("/static/js/app.js")
        self.assertEqual(r.status_code, 200)
        r = c.get("/static/css/style.css")
        self.assertEqual(r.status_code, 200)
        r = c.get("/static/img/favicon.png")
        self.assertEqual(r.status_code, 200)
        print("  ✅ index.html / app.js / style.css / favicon 都能正常取得")

    def test_case_list_shape_matches_frontend_expectations(self):
        c = self.client
        r = c.get("/api/viewer/cases")
        self.assertEqual(r.status_code, 200)
        cases = r.json()
        self.assertEqual(len(cases), 1)
        case = cases[0]
        # app.js loadCases() 需要這些欄位
        for field in ("name", "simulation_type", "folder", "result", "modified_at"):
            self.assertIn(field, case)
        self.assertEqual(case["name"], "TH12_DEMO")
        self.assertEqual(case["result"]["mean_Cl/Cd"], "2.4067")
        # 驗證 humanizeKey 邏輯會用到的欄位確實存在且順序符合 postprocess.java 寫入順序
        keys = list(case["result"].keys())
        self.assertEqual(keys[0], "mean_Cl/Cd")
        self.assertEqual(keys[-1], "Aero_balance_z")
        print(f"  ✅ Case List 資料格式正確，result 欄位順序: {keys}")

    def test_slices_endpoint_matches_frontend_expectations(self):
        c = self.client
        r = c.get("/api/viewer/case/TH12_DEMO/slices/x%20direction%20velocity")
        self.assertEqual(r.status_code, 200)
        names = r.json()
        self.assertEqual(len(names), 3, "app.js 用陣列長度算 sliceFilesMain.length")

        # app.js sliceUrl() 组的 URL 格式
        r = c.get("/api/viewer/case/TH12_DEMO/slice/x%20direction%20velocity/0")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")
        print(f"  ✅ Slices API 正確回傳 {len(names)} 張圖片，單張圖片取得成功")

    def test_slice_types_endpoint_includes_empty_folders(self):
        """
        驗證動態列出 slice type 的 API：要能列出實際存在的資料夾，
        就算是空的（模擬使用者手動在 NFS 上建了一個新資料夾，還沒有圖片），
        不能寫死固定的六個名稱清單。
        """
        c = self.client
        r = c.get("/api/viewer/case/TH12_DEMO/slice_types")
        self.assertEqual(r.status_code, 200)
        types = r.json()
        self.assertIn("x direction velocity", types, "有圖片的資料夾要出現")
        self.assertIn("z direction vorticity", types, "空資料夾也要出現，不能因為沒圖片就被濾掉")
        print(f"  ✅ slice_types 正確列出（含空資料夾）: {types}")

    def test_csv_endpoint_matches_plotly_trace_format(self):
        c = self.client
        r = c.get("/api/viewer/case/TH12_DEMO/csv")
        self.assertEqual(r.status_code, 200)
        files = r.json()
        self.assertIn("Residuals_0000.000.csv", files)
        self.assertIn("DF_0000.000.csv", files)

        r = c.get("/api/viewer/case/TH12_DEMO/csv/Residuals_0000.000.csv")
        data = r.json()
        # app.js drawOnePlot() 直接用 d.columns / d.rows / d.log_scale
        self.assertIn("columns", data)
        self.assertIn("rows", data)
        self.assertIn("log_scale", data)
        self.assertTrue(data["log_scale"], "Residuals 檔名應該被判定為 log scale")
        self.assertEqual(data["columns"][0], "Iteration")
        self.assertEqual(len(data["rows"]), 3)
        self.assertEqual(data["rows"][0], [1.0, 0.114, 0.038])

        r = c.get("/api/viewer/case/TH12_DEMO/csv/DF_0000.000.csv")
        data2 = r.json()
        self.assertFalse(data2["log_scale"], "DF 檔名應該被判定為 linear scale")
        print(f"  ✅ CSV API 格式正確：columns={data['columns']}, log_scale(Residuals)={data['log_scale']}, log_scale(DF)={data2['log_scale']}")

    def test_stl_endpoint_returns_parseable_binary(self):
        c = self.client
        r = c.get("/api/viewer/case/TH12_DEMO/stl")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "model/stl")
        body = r.content
        # STL 二進位格式：80 bytes header + 4 bytes triangle count + (50 bytes * N)
        triangle_count = struct.unpack("<I", body[80:84])[0]
        self.assertEqual(triangle_count, 1)
        self.assertEqual(len(body), 80 + 4 + 50 * triangle_count)
        print(f"  ✅ STL API 回傳合法的二進位 STL（{triangle_count} 個三角形），three.js STLLoader.parse() 可以處理")

    def test_result_endpoint_for_single_case(self):
        c = self.client
        r = c.get("/api/viewer/case/TH12_DEMO/result")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["mean_FW_DF"], "50.6073")
        print("  ✅ 單一 case 的 Result.dat 端點正常")

    def test_parameters_endpoint_for_single_case(self):
        c = self.client
        r = c.get("/api/viewer/case/TH12_DEMO/parameters")
        self.assertEqual(r.status_code, 200)
        params = r.json()
        self.assertIn("simulation_type", params)
        print(f"  ✅ case_parameter.dat 端點正常: {params}")

    def test_nonexistent_case_returns_404_not_500(self):
        c = self.client
        r = c.get("/api/viewer/case/NOT_A_REAL_CASE/result")
        self.assertEqual(r.status_code, 404, "前端遇到不存在的 case 應該收到 404 而不是崩潰的 500")
        print("  ✅ 不存在的 case 正確回傳 404")

    def test_vendor_scripts_are_self_hosted_and_correctly_ordered(self):
        """
        迴歸測試：防止「黑畫面」bug 再次出現。

        根因：Alpine.js 用 defer 載入，會在 x-data="platform()" 的
        <body> 上自動初始化；如果 app.js（定義 platform 函式）排在
        Alpine 後面，Alpine 執行時 platform 還不存在，整頁會卡在
        x-cloak/x-show 的隱藏狀態，變成一片黑畫面且沒有任何提示。

        這支測試直接斷言：
          1. 所有 JS 都是本機 vendor 檔案，不依賴外部 CDN
          2. app.js 一定排在 alpine.min.js 前面
          3. three.min.js 一定排在 STLLoader.js 前面（STLLoader 依賴全域 THREE）
          4. 失敗提示的 UI（boot-error）確實存在，不會讓使用者只看到黑畫面
        """
        c = self.client
        html = c.get("/").text

        self.assertNotIn("cdn.jsdelivr.net", html, "不應該還依賴外部 CDN")
        self.assertNotIn("cdn.plot.ly", html, "不應該還依賴外部 CDN")

        import re
        script_srcs = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
        self.assertEqual(
            script_srcs,
            [
                "/static/vendor/plotly.min.js",
                "/static/vendor/three.min.js",
                "/static/vendor/STLLoader.js",
                "/static/js/app.js",
                "/static/vendor/alpine.min.js",
            ],
            "script 載入順序不對，可能重現黑畫面 bug",
        )

        app_js_index = script_srcs.index("/static/js/app.js")
        alpine_index = script_srcs.index("/static/vendor/alpine.min.js")
        self.assertLess(app_js_index, alpine_index, "app.js（定義 platform）必須排在 Alpine 前面")

        three_index = script_srcs.index("/static/vendor/three.min.js")
        stl_index = script_srcs.index("/static/vendor/STLLoader.js")
        self.assertLess(three_index, stl_index, "three.min.js 必須排在 STLLoader.js 前面")

        self.assertIn('id="boot-error"', html, "缺少初始化失敗提示的 UI，出錯時使用者會看到黑畫面而不知道原因")
        self.assertIn("__bootErrors", html, "缺少 script 載入失敗的錯誤捕捉機制")

        for src in script_srcs:
            r = c.get(src)
            self.assertEqual(r.status_code, 200, f"{src} 沒有正確被伺服")
            self.assertGreater(len(r.content), 100, f"{src} 內容看起來是空的")

        print(f"  ✅ script 順序正確: {' → '.join(s.split('/')[-1] for s in script_srcs)}")
        print("  ✅ 全部 JS 都是本機 vendor 檔案，不依賴外部 CDN")
        print("  ✅ boot-error 失敗提示 UI 存在")


if __name__ == "__main__":
    unittest.main(verbosity=2)
