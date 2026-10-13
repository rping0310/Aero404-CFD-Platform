from fastapi import APIRouter, Depends

from ..config import settings
from ..dependencies import current_user, manager, monitor
from ..session import Session

router = APIRouter(prefix="/api/servers", tags=["servers"])


@router.get("")
def list_servers(user: Session = Depends(current_user), queue=Depends(manager), health=Depends(monitor)):
    result = []
    for server in settings.get_enabled_servers():
        item = {k: v for k, v in server.items() if k not in {"ssh_key_path"}}
        item["used_cores"] = queue.used_cores(server["name"])
        item["available_cores"] = queue.available_cores(server["name"])
        item["metrics"] = health.server_metrics.get(server["name"], {})
        result.append(item)
    return result
