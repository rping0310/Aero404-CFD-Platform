from __future__ import annotations

import csv
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..config import settings
from ..dependencies import current_user
from ..session import Session

router = APIRouter(prefix="/api/viewer", tags=["viewer"])


def _root() -> Path:
    return Path(settings.get_host_nfs_root()).resolve()


def _case(case_name: str) -> Path:
    if Path(case_name).name != case_name or ".." in case_name:
        raise HTTPException(400, "Invalid case name")
    root = _root()
    folders = settings.get_app_settings().get("simulation_type_folders", {}).values()
    matches = [(root / str(folder) / case_name).resolve() for folder in folders]
    for path in matches:
        if path.parent.parent == root and path.is_dir():
            return path
    raise HTTPException(404, f"Case not found: {case_name}")


def _parse_key_value(path: Path) -> dict[str, str]:
    return settings.parse_key_value_file(path)


def _child(base: Path, name: str) -> Path:
    if Path(name).name != name or ".." in name:
        raise HTTPException(400, "Invalid file name")
    base = base.resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise HTTPException(400, "Path escapes the case directory")
    return path


@router.get("/cases")
def cases(user: Session = Depends(current_user)):
    root = _root()
    output = []
    for sim_type, folder in settings.get_app_settings().get("simulation_type_folders", {}).items():
        parent = root / str(folder)
        if not parent.is_dir():
            continue
        template = settings.get_app_settings().get("template_folder_name")
        for path in sorted(p for p in parent.iterdir() if p.is_dir() and p.name != template):
            output.append({
                "name": path.name, "simulation_type": int(sim_type), "folder": str(folder),
                "result": _parse_key_value(path / "Result.dat"),
                "modified_at": path.stat().st_mtime,
            })
    return output


@router.get("/case/{case_name}/result")
def result(case_name: str, user: Session = Depends(current_user)):
    path = _case(case_name) / "Result.dat"
    if not path.is_file():
        raise HTTPException(404, "Result.dat has not been generated yet")
    return _parse_key_value(path)


@router.get("/case/{case_name}/parameters")
def parameters(case_name: str, user: Session = Depends(current_user)):
    """回傳這個 case 資料夾裡的 case_parameter.dat 內容，給 Slices/Plots 頁面
    下方的迷你參數表格用。"""
    path = _case(case_name) / "case_parameter.dat"
    if not path.is_file():
        raise HTTPException(404, "case_parameter.dat has not been generated yet")
    return _parse_key_value(path)


@router.get("/case/{case_name}/slice_types")
def slice_types(case_name: str, user: Session = Depends(current_user)):
    """
    列出這個 case 資料夾底下實際存在的 slice 子資料夾（就算是空的也列出來，
    例如使用者手動在 NFS 上新增一個 /newfolder，也應該出現在下拉選單裡）。
    不寫死固定的六個名稱清單，直接掃描實際存在的資料夾，
    這樣之後 postprocess.java 增加新的輸出類型也不用改前端。
    """
    case_folder = _case(case_name)
    return sorted(p.name for p in case_folder.iterdir() if p.is_dir())


@router.get("/case/{case_name}/slices/{slice_type}")
def slices(case_name: str, slice_type: str, user: Session = Depends(current_user)):
    if Path(slice_type).name != slice_type or ".." in slice_type:
        raise HTTPException(400, "Invalid slice type")
    folder = _child(_case(case_name), slice_type)
    if not folder.is_dir():
        return []
    return [p.name for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() == ".png"]


@router.get("/case/{case_name}/slice/{slice_type}/{index}")
def slice_image(case_name: str, slice_type: str, index: int, user: Session = Depends(current_user)):
    names = slices(case_name, slice_type, user)
    if index < 0 or index >= len(names):
        raise HTTPException(404, "Slice index out of range")
    return FileResponse(_child(_child(_case(case_name), slice_type), names[index]), media_type="image/png")


@router.get("/case/{case_name}/csv")
def csv_files(case_name: str, user: Session = Depends(current_user)):
    return [p.name for p in sorted(_case(case_name).iterdir()) if p.is_file() and p.suffix.lower() == ".csv"]


@router.get("/case/{case_name}/csv/{filename}")
def csv_data(case_name: str, filename: str, user: Session = Depends(current_user)):
    if Path(filename).name != filename or not filename.lower().endswith(".csv"):
        raise HTTPException(400, "Invalid CSV filename")
    path = _child(_case(case_name), filename)
    if not path.is_file():
        raise HTTPException(404, "CSV not found")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return {"columns": [], "rows": [], "log_scale": False}
    columns = [cell.strip().strip('"') for cell in rows[0]]
    values = []
    for row in rows[1:]:
        parsed = []
        for cell in row:
            cell = cell.strip().strip('"')
            try:
                parsed.append(float(cell))
            except ValueError:
                parsed.append(cell)
        values.append(parsed)
    lower = filename.lower()
    return {"columns": columns, "rows": values, "log_scale": "residual" in lower or "resid" in lower}


@router.get("/case/{case_name}/stl")
def stl(case_name: str, user: Session = Depends(current_user)):
    path = _child(_case(case_name), "q-criterion.stl")
    if not path.is_file():
        raise HTTPException(404, "q-criterion.stl has not been generated yet")
    return FileResponse(path, media_type="model/stl", filename=path.name)
