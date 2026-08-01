from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from backend.app.api.deps import current_user, settings_dep
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.services.backups import BackupService

router = APIRouter(prefix="/system/backups", tags=["backups"])


@router.get("")
def list_backups(
    settings: Settings = Depends(settings_dep),
    _user: User = Depends(current_user),
) -> dict:
    service = BackupService(settings)
    return {"mode": "application" if service.supported else "external_required", "items": service.list()}


@router.post("", status_code=201)
def create_backup(
    settings: Settings = Depends(settings_dep),
    _user: User = Depends(current_user),
) -> dict:
    return BackupService(settings).create()


@router.post("/{name}/verify")
def verify_backup(
    name: str,
    settings: Settings = Depends(settings_dep),
    _user: User = Depends(current_user),
) -> dict:
    return BackupService(settings).verify(name)


@router.get("/{name}/download")
def download_backup(
    name: str,
    settings: Settings = Depends(settings_dep),
    _user: User = Depends(current_user),
) -> FileResponse:
    path = BackupService(settings).download_path(name)
    return FileResponse(path, filename=path.name, media_type="application/zip")
