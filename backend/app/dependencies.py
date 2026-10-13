from fastapi import Cookie, Depends, HTTPException, Request, status

from .session import Session, sessions


def current_user(cfd_session: str | None = Cookie(default=None)) -> Session:
    session = sessions.get(cfd_session)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please log in")
    return session


def admin_user(user: Session = Depends(current_user)) -> Session:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user


def job_editor_user(user: Session = Depends(current_user)) -> Session:
    """admin 或 user 都可以（新增 case、編輯 note），visitor 不行（唯讀）。"""
    if user.role not in ("admin", "user"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Read-only account cannot perform this action")
    return user


def manager(request: Request):
    return request.app.state.queue_manager


def monitor(request: Request):
    return request.app.state.monitor
