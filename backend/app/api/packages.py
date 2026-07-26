"""Project package export/import routes."""

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..services.project_packages import export_project_package, import_project_package


router = APIRouter(prefix="/api")
_exports: dict[str, Path] = {}


@router.post("/projects/{project_id}/package")
def create_package(project_id: str, include_media: bool = False):
    try:
        path = export_project_package(project_id, include_media)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    token = path.stem
    _exports[token] = path
    return {"package_id": token, "filename": path.name, "size": path.stat().st_size}


@router.get("/project-packages/{package_id}/download")
def download_package(package_id: str):
    path = _exports.get(package_id)
    if not path or not path.is_file():
        raise HTTPException(404, "项目包不存在或已过期")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.post("/project-packages/import", status_code=201)
async def import_package(file: UploadFile = File(...)):
    if Path(file.filename or "").suffix.lower() != ".sfproject":
        raise HTTPException(422, "请选择 .sfproject 项目包")
    descriptor, temporary = tempfile.mkstemp(suffix=".sfproject")
    os.close(descriptor)
    try:
        size = 0
        with open(temporary, "wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 50 * 1024**3:
                    raise HTTPException(413, "项目包超过 50 GB")
                output.write(chunk)
        return import_project_package(temporary)
    except ValueError as error:
        raise HTTPException(422, detail={"code": "PROJECT_PACKAGE_INVALID", "message": str(error), "recoverable": True}) from error
    finally:
        Path(temporary).unlink(missing_ok=True)
