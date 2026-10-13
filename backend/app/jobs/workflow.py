"""
Workflow Executor：處理「新增 Case」的檔案準備工作。

職責分工：
  - WorkflowExecutor：複製 template → 改名 .sim → 放上傳檔案 → 寫 case_parameter.dat
    這些是瞬間完成的磁碟操作，不佔核心，不需要排隊。
  - JobQueueManager：拿到準備好的 case 資料夾後，決定何時啟動 starccm+、分配幾顆核心。

所有路徑都從 app_settings.yaml 和 servers.yaml 動態讀取，不寫死。
"""

import re
import shutil
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from ..config import settings


@dataclass
class CaseCreationResult:
    ok: bool
    case_folder: str = ""
    command_template: str = ""
    error: str = ""


class WorkflowExecutor:

    def prepare_case(
        self,
        case_name: str,
        simulation_type: int,
        server_name: str,
        uploaded_file_path: Optional[Path],
        uploaded_file_name: Optional[str],
        case_parameter_content: dict[str, str],
        force_overwrite: bool = False,
    ) -> CaseCreationResult:
        """
        執行建立 Case 的所有檔案操作。

        Args:
            case_name: 使用者填的名稱（例如 TH12_002），會變成資料夾名和 .sim 檔名
            simulation_type: 1/2/3，決定去 Straight/Yaw/Cornering 哪個資料夾
            server_name: 選的 server，用來查 nfs_path
            uploaded_file_path: 上傳的 TH1X_XXX.x_t 的暫存位置（可能 None，如果沒上傳）
            uploaded_file_name: 上傳檔案的原始檔名
            case_parameter_content: 使用者填好（或用預設值）的 case_parameter 內容
            force_overwrite: 如果資料夾已存在且佇列允許覆蓋，是否強制覆蓋

        Returns:
            CaseCreationResult: 包含 case_folder 絕對路徑和待填入 {{cores}} 的指令模板
        """
        try:
            app = settings.get_app_settings()

            # 1. 組出路徑
            sim_type_folders = app.get("simulation_type_folders", {})
            sim_type_folder = sim_type_folders.get(simulation_type) or sim_type_folders.get(str(simulation_type))
            if not sim_type_folder:
                return CaseCreationResult(ok=False, error=f"No folder mapped for simulation_type={simulation_type}")

            server_cfg = self._get_server_config(server_name)
            if server_cfg is None:
                return CaseCreationResult(ok=False, error=f"Server '{server_name}' not found")

            # 這裡刻意分成兩種路徑視角，不能混用：
            #
            # host_nfs_root：跑這個 Web Service 的機器自己看到的 NFS 路徑。
            #   所有實際的檔案操作（複製 template、rename、寫入 case_parameter.dat）
            #   都是由「這個 Python 進程」執行，不管使用者選的目標 server 是誰，
            #   執行的機器永遠是同一台（跑 uvicorn 的那台），所以檔案操作一律要用
            #   這個路徑，不能用目標 server 自己的 nfs_path。
            #
            # target_nfs_root：使用者選定的目標 server（可能是遠端）自己看到的
            #   NFS 路徑。這個只用來組「之後要在目標 server 上執行的指令」會用到
            #   的 cwd（SSH 過去之後 cd 到哪裡），不能拿來做本機檔案操作 ——
            #   如果目標是遠端主機，這個路徑字串在本機這台機器上通常根本不存在。
            #
            # 如果目標就是本機（host == target），這兩個會自然相等，行為跟之前一樣。
            host_nfs_root = self._host_nfs_root()
            target_nfs_root = Path(server_cfg["nfs_path"])

            template_folder_name = app.get("template_folder_name", "")
            host_template_folder = host_nfs_root / sim_type_folder / template_folder_name
            host_case_folder = host_nfs_root / sim_type_folder / case_name
            target_case_folder = target_nfs_root / sim_type_folder / case_name

            # 2. 檢查 template 存在（一定用 host 視角看，因為複製動作是 host 在做）
            if not host_template_folder.exists():
                return CaseCreationResult(
                    ok=False,
                    error=f"Template folder does not exist: {host_template_folder}"
                )

            # 3. 檢查目標資料夾是否存在（同樣用 host 視角，這是本機檔案系統的實際狀態）
            if host_case_folder.exists() and not force_overwrite:
                return CaseCreationResult(
                    ok=False,
                    error=f"FOLDER_EXISTS:{case_name}"  # 特殊 error code，讓 API 層判斷要不要跳警告
                )

            # 4. 找到 template 裡的 .sim 檔案名稱
            template_sim_file = self._find_sim_file(host_template_folder)
            if template_sim_file is None:
                return CaseCreationResult(
                    ok=False,
                    error=f"No .sim file found in template folder: {host_template_folder}"
                )

            # 5. 複製 template → case_folder（全部用 host 視角操作實體檔案）
            if host_case_folder.exists():
                shutil.rmtree(host_case_folder)
            shutil.copytree(str(host_template_folder), str(host_case_folder))

            # 6. 把 template 的 .sim rename 成 case_name.sim
            old_sim = host_case_folder / template_sim_file.name
            new_sim = host_case_folder / f"{case_name}.sim"
            if old_sim.exists():
                old_sim.rename(new_sim)

            # 7. 放上傳的 parasolid 檔案
            if uploaded_file_path and uploaded_file_name:
                dest = host_case_folder / uploaded_file_name
                shutil.copy2(str(uploaded_file_path), str(dest))

            # 8. 寫入 case_parameter.dat（覆蓋 template 裡的預設版本）
            settings.write_key_value_file(
                host_case_folder / "case_parameter.dat",
                case_parameter_content,
            )

            # 9. 組出指令模板（{{cores}} 留給 queue_manager 在真正啟動時填入）
            entry_macro = app.get("entry_macro", "batch_run.java")
            starccm_path = server_cfg.get("starccm_path", "starccm+")
            command_template = f"{starccm_path} -np {{{{cores}}}} -batch {entry_macro} {case_name}.sim"

            # case_folder 回傳的是「目標 server 視角」的路徑 —— 這個值會變成
            # job.work_dir，是 real_runner.py 執行 SSH 指令時 cd 的目的地，
            # 必須是目標主機自己看得懂的路徑，不是 host 自己的路徑。
            # 如果目標就是本機，target_case_folder == host_case_folder，沒有差異。
            return CaseCreationResult(
                ok=True,
                case_folder=str(target_case_folder),
                command_template=command_template,
            )

        except Exception as e:
            return CaseCreationResult(ok=False, error=str(e))

    def validate_uploaded_filename(self, filename: str) -> bool:
        """檢查上傳的檔名是否符合 app_settings 裡設定的 pattern。"""
        app = settings.get_app_settings()
        pattern = app.get("uploaded_file_pattern", r".*\.x_t$")
        return bool(re.fullmatch(pattern, filename, re.IGNORECASE))

    def _get_server_config(self, server_name: str) -> Optional[dict]:
        for s in settings.get_servers():
            if s["name"] == server_name:
                return s
        return None

    def _host_nfs_root(self) -> Path:
        """
        回傳「執行這個 Web Service 的機器」自己看到的 NFS 根目錄。

        判斷方式：servers.yaml 裡 host 是 127.0.0.1/localhost 的那筆設定，
        就代表這台機器自己 —— 跟 real_runner.py 的 _is_local() 用同一套慣例，
        兩邊要保持一致：每個部署都必須有剛好一筆「本機」的 server 設定，
        代表跑 uvicorn 的這台機器。
        """
        for s in settings.get_servers():
            if str(s.get("host", "")).lower() in {"127.0.0.1", "localhost", "::1"}:
                return Path(s["nfs_path"])
        raise RuntimeError(
            "servers.yaml 裡找不到 host 是 127.0.0.1/localhost 的本機 server 設定，"
            "無法判斷這台 Web Service 機器自己看到的 NFS 路徑"
        )

    def _find_sim_file(self, folder: Path) -> Optional[Path]:
        """找資料夾裡第一個 .sim 檔案。"""
        for f in folder.iterdir():
            if f.is_file() and f.suffix == ".sim":
                return f
        return None
