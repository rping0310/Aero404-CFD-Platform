"""
Hot-reloading config loader.

所有設定檔(servers.yaml, users.yaml, app_settings.yaml, workflow_template.yaml)
都透過這個模組讀取。核心邏輯：比對檔案 mtime，變了才重新讀取，
不需要重啟服務，也不需要呼叫任何 API 去通知 reload。

同時提供 mtime 比對機制，讓網站表單編輯 app_settings 時可以偵測
「我開表單之後，有沒有人從後端直接改過這份檔案」，避免互相覆蓋。
"""

import yaml
import threading
from pathlib import Path
from typing import Any, Callable, Optional


class HotReloadFile:
    """
    包一份設定檔：記住路徑、上次讀取的 mtime、快取的內容。
    get() 呼叫時才檢查 mtime，變了就重新讀，沒變就回快取，
    所以就算被呼叫很頻繁也不會一直重複讀硬碟。
    """

    def __init__(self, path: Path, default_factory: Callable[[], Any] = dict):
        self.path = Path(path)
        self.default_factory = default_factory
        self._lock = threading.Lock()
        self._cached_content: Any = None
        self._cached_mtime: Optional[float] = None

    def _read_raw(self) -> Any:
        if not self.path.exists():
            return self.default_factory()
        with self.path.open("r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
        return content if content is not None else self.default_factory()

    def get(self) -> Any:
        """回傳目前內容；檔案有變動才重新讀取。"""
        with self._lock:
            if not self.path.exists():
                self._cached_content = self.default_factory()
                self._cached_mtime = None
                return self._cached_content

            current_mtime = self.path.stat().st_mtime
            if self._cached_mtime is None or current_mtime != self._cached_mtime:
                self._cached_content = self._read_raw()
                self._cached_mtime = current_mtime

            return self._cached_content

    def get_mtime(self) -> Optional[float]:
        """給網站表單用：開啟編輯畫面時記錄目前的 mtime。"""
        if not self.path.exists():
            return None
        return self.path.stat().st_mtime

    def write(self, content: Any, expected_mtime: Optional[float] = None) -> None:
        """
        寫入新內容。

        expected_mtime: 如果有帶，且目前檔案的 mtime 跟這個不一致，
        代表檔案在使用者編輯期間被別的地方（後端手動編輯、或另一個
        使用者）動過，拋出 ConflictError 讓上層決定怎麼處理，
        而不是靜默覆蓋掉別人的修改。
        """
        with self._lock:
            if expected_mtime is not None and self.path.exists():
                actual_mtime = self.path.stat().st_mtime
                if actual_mtime != expected_mtime:
                    raise ConfigConflictError(
                        f"{self.path.name} 已被其他地方修改，請重新載入後再儲存"
                    )

            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(content, f, allow_unicode=True, sort_keys=False)

            # 立刻更新快取，避免下一次 get() 又去讀一次硬碟
            self._cached_content = content
            self._cached_mtime = self.path.stat().st_mtime


class ConfigConflictError(Exception):
    """表單儲存時發現檔案已被其他地方修改。"""
    pass
