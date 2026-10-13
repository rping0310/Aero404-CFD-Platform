from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..config.loader import ConfigConflictError
from ..dependencies import admin_user, current_user
from ..session import Session

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsBody(BaseModel):
    content: dict
    expected_mtime: float | None = None


class ParameterBody(BaseModel):
    content: dict[str, str]
    expected_mtime: float | None = None


@router.get("")
def get_settings(user: Session = Depends(current_user)):
    return {"content": settings.get_app_settings(), "mtime": settings.APP_SETTINGS_FILE.get_mtime()}


@router.put("")
def put_settings(body: SettingsBody, user: Session = Depends(admin_user)):
    try:
        settings.APP_SETTINGS_FILE.write(body.content, body.expected_mtime)
    except ConfigConflictError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "mtime": settings.APP_SETTINGS_FILE.get_mtime()}


@router.get("/servers")
def get_servers(user: Session = Depends(admin_user)):
    return {"content": {"servers": settings.get_servers()}, "mtime": settings.SERVERS_FILE.get_mtime()}


@router.put("/servers")
def put_servers(body: SettingsBody, user: Session = Depends(admin_user)):
    servers = body.content.get("servers")
    if not isinstance(servers, list) or any(not isinstance(item, dict) or not item.get("name") for item in servers):
        raise HTTPException(422, "content.servers must be a list of servers with a name field")
    try:
        settings.SERVERS_FILE.write(body.content, body.expected_mtime)
    except ConfigConflictError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "mtime": settings.SERVERS_FILE.get_mtime()}


@router.get("/case_parameter_default")
def get_parameters(user: Session = Depends(current_user)):
    path = settings.CASE_PARAMETER_DEFAULT_PATH
    return {"content": settings.get_case_parameter_defaults(), "mtime": path.stat().st_mtime if path.exists() else None}


@router.put("/case_parameter_default")
def put_parameters(body: ParameterBody, user: Session = Depends(admin_user)):
    path = settings.CASE_PARAMETER_DEFAULT_PATH
    actual = path.stat().st_mtime if path.exists() else None
    if body.expected_mtime is not None and actual != body.expected_mtime:
        raise HTTPException(409, "case_parameter_default.dat has been modified, please reload")
    settings.write_key_value_file(path, body.content)
    return {"ok": True, "mtime": path.stat().st_mtime}
