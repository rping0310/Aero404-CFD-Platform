"""
Job 資料模型。狀態只有四種：WAITING / SOLVING / DONE / FAILED
（不細分 meshing/solving/postprocessing，太細沒必要，見專案討論）。

killed 是額外的 bool flag，疊加在 FAILED 之上，用來在 UI 上跟「自然失敗」
區分開（admin 手動 kill vs. starccm+ 自己跑到一半死掉）。
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
from datetime import datetime
import uuid


class JobStatus(str, Enum):
    WAITING = "WAITING"
    SOLVING = "SOLVING"
    DONE = "DONE"
    FAILED = "FAILED"


class CoreMode(str, Enum):
    HALF = "half"
    ALL = "all"


@dataclass
class Job:
    case_name: str
    server_name: str
    mode: CoreMode
    added_by: str
    status: JobStatus = JobStatus.WAITING
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    cores_allocated: Optional[int] = None   # 決定於實際開始跑的那一刻
    pid: Optional[int] = None
    command_template: Optional[str] = None  # 含未填入的 {{cores}}，由 WorkflowExecutor 準備好
    work_dir: Optional[str] = None
    queue_position: int = 0                 # 同一台 server 的 WAITING 排序，admin 可調整
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    killed: bool = False
    error_message: Optional[str] = None
    simulation_type: Optional[int] = None   # 1/2/3，給 Job Session 的 Type 欄位用
    note: Optional[str] = None              # 使用者可自由填寫、事後也可編輯的備註

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        d = dict(d)
        d["mode"] = CoreMode(d["mode"])
        d["status"] = JobStatus(d["status"])
        return cls(**d)

    def runtime_seconds(self) -> Optional[float]:
        """從開始跑到現在（或結束）經過幾秒。還沒開始跑回傳 None。"""
        if self.started_at is None:
            return None
        start = datetime.fromisoformat(self.started_at)
        end = datetime.fromisoformat(self.finished_at) if self.finished_at else datetime.now()
        return (end - start).total_seconds()
