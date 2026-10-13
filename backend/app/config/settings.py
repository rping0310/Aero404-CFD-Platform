"""
所有設定檔的統一存取入口。

檔案位置全部集中在 DATA_DIR，方便部署腳本一次備份/掛載這一個資料夾。
每份設定檔都是 HotReloadFile，改檔案不用重啟服務。

新增一台 server：直接編輯 servers.yaml，下一次任何 API 呼叫
get_servers() 就會看到新的一台，不需要重啟 systemd service。
"""

import os
from pathlib import Path
from .loader import HotReloadFile

# 資料目錄可透過環境變數覆蓋，方便不同團隊部署時指到自己的路徑
DATA_DIR = Path(os.environ.get("CFD_PLATFORM_DATA_DIR", Path(__file__).parent.parent.parent.parent / "data"))

SERVERS_FILE = HotReloadFile(DATA_DIR / "servers.yaml", default_factory=lambda: {"servers": []})
USERS_FILE = HotReloadFile(DATA_DIR / "users.yaml", default_factory=lambda: {"users": []})
APP_SETTINGS_FILE = HotReloadFile(DATA_DIR / "app_settings.yaml", default_factory=dict)
WORKFLOW_FILE = HotReloadFile(DATA_DIR / "workflow_template.yaml", default_factory=lambda: {"steps": []})

# case_parameter_default.dat 不是 YAML，是 key = value 格式（跟 STAR-CCM+ 的
# case_parameter.dat 格式完全一樣），所以不用 HotReloadFile / yaml，另外處理。
CASE_PARAMETER_DEFAULT_PATH = DATA_DIR / "case_parameter_default.dat"


def get_servers() -> list[dict]:
    """回傳目前註冊的所有 server 設定（含停用的，篩選交給呼叫端）。"""
    return SERVERS_FILE.get().get("servers", [])


def get_enabled_servers() -> list[dict]:
    return [s for s in get_servers() if s.get("enabled", True)]


def get_users() -> list[dict]:
    return USERS_FILE.get().get("users", [])


def get_app_settings() -> dict:
    return APP_SETTINGS_FILE.get()


def get_workflow_steps() -> list[dict]:
    return WORKFLOW_FILE.get().get("steps", [])


def parse_key_value_file(path: Path) -> dict[str, str]:
    """
    解析 `key = value` 格式的檔案（case_parameter.dat / case_parameter_default.dat）。
    跳過空行和 # // 開頭的註解行，維持原始寫入順序（Python 3.7+ dict 保序）。
    """
    result: dict[str, str] = {}
    if not path.exists():
        return result

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()

    return result


def write_key_value_file(path: Path, data: dict[str, str]) -> None:
    """把 dict 寫回 `key = value` 格式，保留 dict 的 key 順序。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for key, value in data.items():
            f.write(f"{key} = {value}\n")


def get_case_parameter_defaults() -> dict[str, str]:
    """讀取網站用的預設參數（case_parameter_default.dat），這份只有 Admin 設定頁能改。"""
    return parse_key_value_file(CASE_PARAMETER_DEFAULT_PATH)


def get_host_nfs_root() -> str:
    """回傳『跑這個 Web Service 的機器』自己看到的 NFS 路徑。

    判斷方式：servers.yaml 裡 host 是 127.0.0.1/localhost 的那筆設定，
    跟 real_runner.py 的 _is_local() 用同一套慣例。
    """
    for s in get_servers():
        if str(s.get("host", "")).lower() in {"127.0.0.1", "localhost", "::1"}:
            return s["nfs_path"]
    raise RuntimeError("servers.yaml 裡找不到 host=127.0.0.1 的本機 server 設定")


def get_host_nfs_root() -> str:
    """回傳『跑這個 Web Service 的機器』自己看到的 NFS 路徑。

    判斷方式：servers.yaml 裡 host 是 127.0.0.1/localhost 的那筆設定，
    跟 real_runner.py 的 _is_local() 用同一套慣例。
    """
    for s in get_servers():
        if str(s.get("host", "")).lower() in {"127.0.0.1", "localhost", "::1"}:
            return s["nfs_path"]
    raise RuntimeError("servers.yaml 裡找不到 host=127.0.0.1 的本機 server 設定")
