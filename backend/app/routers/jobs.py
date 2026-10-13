from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..dependencies import admin_user, current_user, job_editor_user, manager
from ..config import settings
from ..jobs.models import CoreMode, JobStatus
from ..jobs.workflow import WorkflowExecutor
from ..session import Session

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
workflow = WorkflowExecutor()


def job_json(job) -> dict:
    data = job.to_dict()
    data["runtime_seconds"] = job.runtime_seconds()
    return data


@router.get("")
def list_jobs(server: str | None = None, status: JobStatus | None = None,
              user: Session = Depends(current_user), queue=Depends(manager)):
    return [job_json(job) for job in queue.list_jobs(server_name=server, status=status)]


@router.post("")
async def create_job(
    case_name: str = Form(...), simulation_type: int = Form(...),
    server_name: str = Form(...), mode: CoreMode = Form(...),
    parameters: str = Form(...), force: bool = Form(False),
    note: str = Form(""),
    upload: UploadFile | None = File(default=None),
    user: Session = Depends(job_editor_user), queue=Depends(manager),
):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", case_name) or ".." in case_name:
        raise HTTPException(422, "Case name may only contain letters, numbers, dots, underscores, and hyphens")
    allowed, reason = queue.check_can_overwrite(case_name)
    if not allowed:
        raise HTTPException(409, reason)
    if force and user.role != "admin":
        raise HTTPException(403, "Only admin can overwrite a case")
    try:
        parameter_data = json.loads(parameters)
        if not isinstance(parameter_data, dict):
            raise ValueError
        parameter_data = {str(k): str(v) for k, v in parameter_data.items()}
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(422, "parameters must be a JSON object")
    parameter_data["simulation_type"] = str(simulation_type)

    temp_path: Path | None = None
    upload_name: str | None = None
    try:
        if upload and upload.filename:
            upload_name = Path(upload.filename).name
            if any(sep in upload.filename for sep in ("/", "\\")) or upload_name != upload.filename or not workflow.validate_uploaded_filename(upload_name):
                raise HTTPException(422, "Uploaded filename does not match the required pattern")
            max_bytes = int(settings.get_app_settings().get("max_upload_size_mb", 500)) * 1024 * 1024
            with tempfile.NamedTemporaryFile(delete=False, suffix=".x_t") as handle:
                temp_path = Path(handle.name)
                size = 0
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(413, "Uploaded file exceeds the size limit")
                    handle.write(chunk)

        result = workflow.prepare_case(
            case_name, simulation_type, server_name, temp_path, upload_name,
            parameter_data, force_overwrite=force,
        )
        if not result.ok:
            code = 409 if result.error.startswith("FOLDER_EXISTS:") else 422
            raise HTTPException(code, result.error)
        job = queue.add_job(
            case_name, server_name, mode, user.name,
            command_template=result.command_template, work_dir=result.case_folder,
            simulation_type=simulation_type, note=note.strip() or None,
        )
        return job_json(job)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


@router.delete("/failed")
def clear_failed(server: str | None = None, user: Session = Depends(admin_user), queue=Depends(manager)):
    """一鍵清除所有 FAILED 的 job 紀錄；帶 ?server=xxx 只清那台 server 的。
    注意：這個路由必須排在 /{job_id} 前面，不然 FastAPI 會把 "failed" 當成
    job_id 傳給 /{job_id} 那個路由（因為 FastAPI 依註冊順序比對路徑）。"""
    removed = queue.clear_failed(server)
    return {"removed": removed}


@router.delete("/{job_id}")
def kill_job(job_id: str, user: Session = Depends(admin_user), queue=Depends(manager)):
    try:
        queue.kill_job(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@router.delete("/{job_id}/record")
def remove_job_record(job_id: str, user: Session = Depends(admin_user), queue=Depends(manager)):
    """從歷史紀錄裡永久刪除一筆 DONE/FAILED 的 job（不是 kill，是真的刪掉紀錄）。"""
    try:
        queue.remove_job(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


class ReorderBody(BaseModel):
    server_name: str
    job_ids: list[str]


@router.put("/reorder")
def reorder(body: ReorderBody, user: Session = Depends(admin_user), queue=Depends(manager)):
    queue.reorder_queue(body.server_name, body.job_ids)
    return {"ok": True}


class NoteBody(BaseModel):
    note: str


@router.put("/{job_id}/note")
def update_note(job_id: str, body: NoteBody, user: Session = Depends(job_editor_user), queue=Depends(manager)):
    """更新備註 —— admin 和 user 都可以編輯，visitor（唯讀帳號）不行。"""
    try:
        queue.set_note(job_id, body.note.strip())
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


# ---- LOGKILL_PATCH ----
@router.get("/{job_id}/log")
def job_log(job_id: str, lines: int = 300, page: int = 0,
            user: Session = Depends(current_user), queue=Depends(manager)):
    """Tail of workflow.log for a job (all logged-in roles, read-only)."""
    lines = max(10, min(int(lines), 2000))
    job = queue.get_job(job_id)
    if job is None or not job.work_dir:
        raise HTTPException(404, "Job not found")
    reader = getattr(queue.runner, "read_log_page", None)
    if reader is None:
        raise HTTPException(501, "This runner cannot read logs")
    res = reader(job.server_name, Path(job.work_dir) / "workflow.log", lines, page)
    if res is None:
        raise HTTPException(404, "workflow.log not found or server unreachable")
    text, cur, pages, total = res
    return {"job_id": job.id, "case_name": job.case_name, "status": job.status.value,
            "lines": lines, "page": cur, "pages": pages, "total": total, "text": text}
# ---- /LOGKILL_PATCH ----
