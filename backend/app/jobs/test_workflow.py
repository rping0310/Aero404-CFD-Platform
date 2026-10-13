"""
Workflow Executor 測試：驗證建立 Case 的檔案操作邏輯。

在 /tmp 底下建立模擬的 NFS 結構，測試：
  1. 正常建立 case（複製 template、改名、寫參數、放上傳檔案）
  2. 資料夾已存在的警告 + force 覆蓋
  3. Template 不存在的錯誤處理
  4. 指令模板正確組裝（含 {{cores}} 佔位符）
  5. 上傳檔名驗證

跑法：
    cd backend
    python3 -m app.jobs.test_workflow
"""

import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.test_support import use_temp_data_dir
use_temp_data_dir()   # never touch the real data/ directory

from app.jobs.workflow import WorkflowExecutor
from app.config import settings

# 建立假的 NFS 結構
TEST_NFS = Path(tempfile.gettempdir()) / "aero404_workflow_test"


def setup_fake_nfs():
    """模擬 NFS 目錄結構，包含 template 資料夾。"""
    if TEST_NFS.exists():
        shutil.rmtree(TEST_NFS)

    for sim_type in ["Straight", "Yaw", "Cornering"]:
        template = TEST_NFS / sim_type / "template_CFD"
        template.mkdir(parents=True)

        # 假的 .sim 檔案
        (template / "template.sim").write_text("FAKE_SIM_FILE")

        # 假的 body.x_t 和 unsprung.x_t
        (template / "body.x_t").write_text("FAKE_BODY")
        (template / "unsprung.x_t").write_text("FAKE_UNSPRUNG")

        # 假的 macro
        (template / "batch_run.java").write_text("FAKE_MACRO")
        (template / "gui_run.java").write_text("FAKE_MACRO")

        # 假的 case_parameter_default.dat
        (template / "case_parameter.dat").write_text(
            "simulation_type = 1\nfree_stream_velocity = 12.5\n"
        )

    # 修改 app_settings 讓測試指向假的 NFS
    # 注意：這裡直接改 servers.yaml 的 nfs_path，讓 workflow 讀到正確路徑
    import yaml
    servers_path = settings.SERVERS_FILE.path
    with servers_path.open("r") as f:
        servers_data = yaml.safe_load(f)

    # 記住原始值，測試結束恢復
    original_nfs_paths = {}
    for s in servers_data["servers"]:
        original_nfs_paths[s["name"]] = s["nfs_path"]
        s["nfs_path"] = str(TEST_NFS)

    with servers_path.open("w") as f:
        yaml.safe_dump(servers_data, f, allow_unicode=True, sort_keys=False)

    return original_nfs_paths


def restore_nfs_paths(original_paths):
    import yaml
    servers_path = settings.SERVERS_FILE.path
    with servers_path.open("r") as f:
        servers_data = yaml.safe_load(f)
    for s in servers_data["servers"]:
        if s["name"] in original_paths:
            s["nfs_path"] = original_paths[s["name"]]
    with servers_path.open("w") as f:
        yaml.safe_dump(servers_data, f, allow_unicode=True, sort_keys=False)


def test_1_normal_case_creation():
    print("\n========================================")
    print("測試 1：正常建立 case")
    print("========================================")

    wf = WorkflowExecutor()

    # 建立假的上傳檔案
    uploaded = Path(tempfile.gettempdir()) / "aero404_upload_TH11_999.x_t"
    uploaded.write_text("FAKE_PARASOLID")

    result = wf.prepare_case(
        case_name="TH12_002",
        simulation_type=1,
        server_name="ServerA",
        uploaded_file_path=uploaded,
        uploaded_file_name="TH11_999.x_t",
        case_parameter_content={
            "simulation_type": "1",
            "free_stream_velocity": "30.0",
            "pitch_angle": "2.0",
            "stopping_iteration": "3000",
        },
    )

    assert result.ok, f"建立失敗: {result.error}"
    case_folder = Path(result.case_folder)
    print(f"  Case folder: {case_folder}")

    # 驗證資料夾結構
    assert case_folder.exists(), "Case 資料夾應該存在"
    assert (case_folder / "TH12_002.sim").exists(), ".sim 檔案應該被 rename"
    assert not (case_folder / "template.sim").exists(), "原始 .sim 檔名應該不存在了"
    assert (case_folder / "body.x_t").exists(), "body.x_t 應該被複製過來"
    assert (case_folder / "unsprung.x_t").exists(), "unsprung.x_t 應該被複製過來"
    assert (case_folder / "TH11_999.x_t").exists(), "上傳的檔案應該放進來了"
    assert (case_folder / "batch_run.java").exists(), "macro 應該被複製過來"

    # 驗證 case_parameter.dat 的內容
    params = settings.parse_key_value_file(case_folder / "case_parameter.dat")
    assert params["simulation_type"] == "1"
    assert params["free_stream_velocity"] == "30.0"
    assert params["pitch_angle"] == "2.0"
    print(f"  case_parameter.dat: {params}")

    # 驗證指令模板
    assert "{{cores}}" in result.command_template, "指令模板應該包含 {{cores}} 佔位符"
    assert settings.get_app_settings()["entry_macro"] in result.command_template
    assert "TH12_002.sim" in result.command_template
    print(f"  command_template: {result.command_template}")

    print("  ✅ 正常建立 case，所有檔案都到位")

    uploaded.unlink()


def test_2_folder_exists_warning():
    print("\n========================================")
    print("測試 2：資料夾已存在 → 警告 + force 覆蓋")
    print("========================================")

    wf = WorkflowExecutor()

    # 第一次建（成功）
    result1 = wf.prepare_case(
        case_name="TH12_003",
        simulation_type=2,
        server_name="ServerA",
        uploaded_file_path=None,
        uploaded_file_name=None,
        case_parameter_content={"simulation_type": "2"},
    )
    assert result1.ok
    print("  第一次建立: OK")

    # 第二次建（同名，不 force）→ 應該回傳 FOLDER_EXISTS
    result2 = wf.prepare_case(
        case_name="TH12_003",
        simulation_type=2,
        server_name="ServerA",
        uploaded_file_path=None,
        uploaded_file_name=None,
        case_parameter_content={"simulation_type": "2"},
        force_overwrite=False,
    )
    assert not result2.ok
    assert "FOLDER_EXISTS" in result2.error
    print(f"  第二次建立（不 force）: 正確被擋 → {result2.error}")

    # 第三次建（同名，force=True）→ 應該覆蓋成功
    result3 = wf.prepare_case(
        case_name="TH12_003",
        simulation_type=2,
        server_name="ServerA",
        uploaded_file_path=None,
        uploaded_file_name=None,
        case_parameter_content={"simulation_type": "2", "yaw_angle": "5.0"},
        force_overwrite=True,
    )
    assert result3.ok
    params = settings.parse_key_value_file(Path(result3.case_folder) / "case_parameter.dat")
    assert params.get("yaw_angle") == "5.0", "覆蓋後的參數應該是新的值"
    print(f"  第三次建立（force=True）: 覆蓋成功，新參數: {params}")

    print("  ✅ 覆蓋保護邏輯正確")


def test_3_template_not_found():
    print("\n========================================")
    print("測試 3：Template 不存在的錯誤處理")
    print("========================================")

    wf = WorkflowExecutor()

    # 刪掉 Cornering 的 template 模擬「還沒建好」的情況
    bad_template = TEST_NFS / "Cornering" / "template_CFD"
    if bad_template.exists():
        shutil.rmtree(bad_template)

    result = wf.prepare_case(
        case_name="TH12_004",
        simulation_type=3,
        server_name="ServerA",
        uploaded_file_path=None,
        uploaded_file_name=None,
        case_parameter_content={"simulation_type": "3"},
    )

    assert not result.ok
    assert "does not exist" in result.error
    print(f"  錯誤訊息: {result.error}")
    print("  ✅ Template 不存在時正確報錯，不會 crash")


def test_4_filename_validation():
    print("\n========================================")
    print("測試 4：上傳檔名驗證")
    print("========================================")

    wf = WorkflowExecutor()

    assert wf.validate_uploaded_filename("TH11_999.x_t"), "TH11_999.x_t 應該合法"
    assert wf.validate_uploaded_filename("TH12_001.x_t"), "TH12_001.x_t 應該合法"
    assert wf.validate_uploaded_filename("TH13_500.x_t"), "TH13_500.x_t 應該合法"
    assert not wf.validate_uploaded_filename("body.x_t"), "body.x_t 不符合 TH1X 格式"
    assert not wf.validate_uploaded_filename("random.stl"), "random.stl 不符合"
    assert not wf.validate_uploaded_filename("TH14_001.x_t"), "TH14 不在 [123] 範圍內"

    print("  ✅ TH11/12/13 合法，其他被拒")


def test_5_simulation_type_determines_folder():
    print("\n========================================")
    print("測試 5：simulation_type 正確對應資料夾")
    print("========================================")

    wf = WorkflowExecutor()

    r1 = wf.prepare_case("test_s", 1, "ServerA", None, None, {"simulation_type": "1"})
    r2 = wf.prepare_case("test_y", 2, "ServerA", None, None, {"simulation_type": "2"})

    assert r1.ok and "Straight" in r1.case_folder
    assert r2.ok and "Yaw" in r2.case_folder

    print(f"  Type 1 → {r1.case_folder}")
    print(f"  Type 2 → {r2.case_folder}")
    print("  ✅ simulation_type 正確對應不同資料夾")


def test_6_remote_server_uses_host_nfs_path_for_file_ops():
    """
    重現真實 bug 場景：host（跑 Web Service 的機器）跟目標 server（遠端）
    NFS 掛載路徑不同（例如 ServerB 掛在 /home/usera/CFD_cloud_NFS，
    ServerA 掛在 /home/userb/CFD_cloud_NFS，同一份 NFS 但本地路徑不同）。

    正確行為：
      - 實際的檔案操作（複製 template、寫入參數）要發生在 host 自己的路徑下
        （因為 workflow.py 是被 host 上的 Python 進程執行的）
      - 回傳給 queue_manager 當作 job.work_dir 的路徑，要是「目標 server」
        自己看到的路徑（因為那是要給 SSH 遠端指令 cd 用的）
    """
    print("\n========================================")
    print("測試 6：host 與目標 server 的 NFS 路徑不同時，檔案操作要用 host 路徑")
    print("========================================")

    host_nfs = Path(tempfile.gettempdir()) / "aero404_host_nfs"
    remote_nfs = Path(tempfile.gettempdir()) / "aero404_remote_nfs"
    for p in (host_nfs, remote_nfs):
        if p.exists():
            shutil.rmtree(p)

    # template 只建在 host 這邊（模擬同一份 NFS，只是兩台機器本地路徑名稱不同）
    template = host_nfs / "Straight" / "template_CFD"
    template.mkdir(parents=True)
    (template / "template.sim").write_text("FAKE_SIM")
    (template / "body.x_t").write_text("FAKE_BODY")
    (template / "unsprung.x_t").write_text("FAKE_UNSPRUNG")
    (template / "batch_run.java").write_text("FAKE_MACRO")

    import yaml
    servers_path = settings.SERVERS_FILE.path
    with servers_path.open("r") as f:
        original_servers_data = yaml.safe_load(f)

    test_servers_data = {
        "servers": [
            {
                "name": "HostMachine", "host": "127.0.0.1", "ssh_user": "x", "ssh_port": 22,
                "ssh_key_path": "~/.ssh/id_ed25519", "total_cores": 40, "reserved_cores": 4,
                "nfs_path": str(host_nfs),
                "starccm_path": "/opt/starccm/starccm+", "enabled": True,
            },
            {
                "name": "RemoteMachine", "host": "192.0.2.10", "ssh_user": "userb", "ssh_port": 22,
                "ssh_key_path": "~/.ssh/id_ed25519", "total_cores": 64, "reserved_cores": 2,
                "nfs_path": str(remote_nfs),  # 刻意跟 host 不同路徑
                "starccm_path": "/home/userb/star/bin/starccm+", "enabled": True,
            },
        ]
    }
    with servers_path.open("w") as f:
        yaml.safe_dump(test_servers_data, f, allow_unicode=True, sort_keys=False)

    try:
        wf = WorkflowExecutor()
        result = wf.prepare_case(
            case_name="TH12_REMOTE_TEST",
            simulation_type=1,
            server_name="RemoteMachine",  # 選的是遠端機器
            uploaded_file_path=None,
            uploaded_file_name=None,
            case_parameter_content={"simulation_type": "1", "free_stream_velocity": "20.0"},
        )

        assert result.ok, f"應該要成功，卻失敗了: {result.error}"

        # 檔案應該真的被寫在 host 路徑下
        host_case_folder = host_nfs / "Straight" / "TH12_REMOTE_TEST"
        assert host_case_folder.exists(), f"檔案應該被複製到 host 路徑: {host_case_folder}"
        assert (host_case_folder / "TH12_REMOTE_TEST.sim").exists()
        assert (host_case_folder / "case_parameter.dat").exists()
        print(f"  ✅ 檔案正確寫在 host 路徑: {host_case_folder}")

        # remote_nfs 底下不應該有任何東西被創建（host 完全沒有能力寫入那個路徑的實體檔案）
        assert not (remote_nfs / "Straight" / "TH12_REMOTE_TEST").exists(), \
            "不應該在 remote_nfs 路徑下真的建立檔案（那是給 SSH 用的邏輯路徑，不是本機路徑）"
        print(f"  ✅ remote_nfs 路徑下沒有被誤寫入任何本機檔案")

        # 回傳的 case_folder（會變成 job.work_dir，給 SSH cd 用）要是「目標」視角
        expected_remote_path = str(remote_nfs / "Straight" / "TH12_REMOTE_TEST")
        assert result.case_folder == expected_remote_path, (
            f"回傳的 case_folder 應該是目標 server 視角的路徑，"
            f"預期 {expected_remote_path}，實際 {result.case_folder}"
        )
        print(f"  ✅ 回傳給 job.work_dir 的路徑正確是目標 server 視角: {result.case_folder}")
        print("  ✅ 沒有出現「Template 資料夾不存在」的誤判")

    finally:
        with servers_path.open("w") as f:
            yaml.safe_dump(original_servers_data, f, allow_unicode=True, sort_keys=False)
        shutil.rmtree(host_nfs, ignore_errors=True)
        shutil.rmtree(remote_nfs, ignore_errors=True)


if __name__ == "__main__":
    original_paths = setup_fake_nfs()
    try:
        test_1_normal_case_creation()
        test_2_folder_exists_warning()
        test_3_template_not_found()
        test_4_filename_validation()
        test_5_simulation_type_determines_folder()
    finally:
        restore_nfs_paths(original_paths)
        if TEST_NFS.exists():
            shutil.rmtree(TEST_NFS)

    test_6_remote_server_uses_host_nfs_path_for_file_ops()

    print("\n========================================")
    print("✅ Workflow 全部測試通過")
    print("========================================")

