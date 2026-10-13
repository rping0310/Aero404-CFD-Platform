import json, time
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import authenticate
from ..config.settings import DATA_DIR, get_app_settings
from ..dependencies import current_user, admin_user
from ..session import Session, sessions

router = APIRouter(prefix="/api", tags=["auth"])
LOGIN_LOG_PATH = DATA_DIR / "login_log.jsonl"
_WINDOW, _MAX = 300, 5
_failed: dict[str, list[float]] = {}

class LoginBody(BaseModel):
    name: str
    password: str

def _ip(r: Request) -> str:
    return r.client.host if r.client else "unknown"

def _log(name: str, ip: str, ok: bool) -> None:
    e = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "name": name, "ip": ip, "ok": ok}
    with LOGIN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = _ip(request)
    now = time.time()
    attempts = [t for t in _failed.get(ip, []) if now - t < _WINDOW]
    if len(attempts) >= _MAX:
        raise HTTPException(429, "Too many failed attempts, try again later")
    result = authenticate(body.name, body.password)
    if not result.ok:
        attempts.append(now); _failed[ip] = attempts
        _log(body.name, ip, False)
        raise HTTPException(401, result.error)
    _failed.pop(ip, None)
    _log(result.name, ip, True)
    token = sessions.create(result.name, result.role)
    # secure cookies are dropped by browsers on plain http (except localhost); set cookie_secure: false in app_settings.yaml for HTTP-only testing
    response.set_cookie("cfd_session", token, httponly=True, samesite="lax",
                        secure=bool(get_app_settings().get("cookie_secure", True)))
    return {"name": result.name, "role": result.role}

@router.post("/logout")
def logout(response: Response, cfd_session: str | None = Cookie(default=None)):
    sessions.delete(cfd_session)
    response.delete_cookie("cfd_session")
    return {"ok": True}

@router.get("/me")
def me(user: Session = Depends(current_user)):
    return {"name": user.name, "role": user.role}

@router.get("/login-log")
def login_log(user: Session = Depends(admin_user), limit: int = 100):
    if not LOGIN_LOG_PATH.exists():
        return []
    lines = LOGIN_LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines[-limit:]][::-1]
