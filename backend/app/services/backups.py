from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import make_url

from backend.app.core.config import PROJECT_ROOT, Settings
from backend.app.core.errors import AppError

_BACKUP_RE = re.compile(r"^chan-backup-[0-9TZ-]+-[a-f0-9]{8}\.zip$")
_ALLOWED = {"manifest.json", "database.sqlite"}
_MAX_DATABASE_BYTES = 4 * 1024 * 1024 * 1024


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: int
    app_version: str
    alembic_revision: str | None
    created_at: str
    database_sha256: str
    database_size: int
    integrity_result: str
    secrets_removed: bool


class BackupService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.directory = Path(settings.backup_dir).resolve()

    @property
    def supported(self) -> bool:
        return make_url(self.settings.database_url).get_backend_name() == "sqlite"

    def _require_sqlite(self) -> Path:
        if not self.supported:
            raise AppError(
                "backup_external_required",
                "PostgreSQL 需要使用外部 pg_dump/pg_restore 流程",
                status_code=409,
                details={"mode": "external_required"},
            )
        raw = make_url(self.settings.database_url).database
        if not raw:
            raise AppError("backup_database_missing", "SQLite 数据库路径未配置", status_code=500)
        path = Path(raw)
        return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()

    def _path(self, name: str) -> Path:
        if not _BACKUP_RE.fullmatch(name) or Path(name).name != name:
            raise AppError("invalid_backup_name", "备份文件名不合法", status_code=400)
        path = (self.directory / name).resolve()
        if path.parent != self.directory:
            raise AppError("invalid_backup_path", "备份路径越界", status_code=400)
        return path

    def list(self) -> list[dict[str, Any]]:
        if not self.supported:
            return []
        self.directory.mkdir(parents=True, exist_ok=True)
        return [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            }
            for path in sorted(
                self.directory.glob("chan-backup-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True
            )
            if _BACKUP_RE.fullmatch(path.name)
        ]

    def create(self) -> dict[str, Any]:
        source = self._require_sqlite()
        if not source.is_file():
            raise AppError("backup_database_missing", "SQLite 数据库文件不存在", status_code=404)
        self.directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        name = f"chan-backup-{timestamp}-{os.urandom(4).hex()}.zip"
        destination = self._path(name)
        with tempfile.TemporaryDirectory(prefix="chan-backup-") as temporary:
            snapshot = Path(temporary) / "database.sqlite"
            source_connection = sqlite3.connect(source)
            target_connection = sqlite3.connect(snapshot)
            try:
                source_connection.backup(target_connection)
                target_connection.execute("DELETE FROM secret_settings")
                target_connection.commit()
                integrity = str(target_connection.execute("PRAGMA integrity_check").fetchone()[0])
                revision_row = (
                    target_connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
                    if self._has_table(target_connection, "alembic_version")
                    else None
                )
            finally:
                target_connection.close()
                source_connection.close()
            digest = self._sha256(snapshot)
            manifest = BackupManifest(
                format_version=1,
                app_version=self.settings.app_version,
                alembic_revision=str(revision_row[0]) if revision_row else None,
                created_at=datetime.now(UTC).isoformat(),
                database_sha256=digest,
                database_size=snapshot.stat().st_size,
                integrity_result=integrity,
                secrets_removed=True,
            )
            pending = destination.with_suffix(".pending")
            try:
                with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
                    archive.write(snapshot, "database.sqlite")
                os.replace(pending, destination)
            finally:
                pending.unlink(missing_ok=True)
        return {"name": name, "manifest": manifest.model_dump(), "size": destination.stat().st_size}

    def verify(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        if not path.is_file():
            raise AppError("backup_not_found", "备份不存在", status_code=404)
        with tempfile.TemporaryDirectory(prefix="chan-verify-") as temporary:
            database = Path(temporary) / "database.sqlite"
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if names != _ALLOWED or any(Path(item).name != item for item in names):
                    raise AppError("backup_unsafe_archive", "备份包包含非白名单或越界路径", status_code=400)
                info = archive.getinfo("database.sqlite")
                if info.file_size > _MAX_DATABASE_BYTES:
                    raise AppError("backup_too_large", "备份数据库超过允许大小", status_code=400)
                manifest = BackupManifest.model_validate_json(archive.read("manifest.json"))
                with archive.open("database.sqlite") as source, database.open("wb") as target:
                    shutil.copyfileobj(source, target)
            if (
                database.stat().st_size != manifest.database_size
                or self._sha256(database) != manifest.database_sha256
            ):
                raise AppError("backup_hash_mismatch", "备份哈希或大小校验失败", status_code=400)
            if manifest.alembic_revision and manifest.alembic_revision not in self._known_revisions():
                raise AppError("backup_revision_incompatible", "备份包含当前应用不认识的迁移版本", status_code=409)
            connection = sqlite3.connect(database)
            try:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                secret_count = (
                    int(connection.execute("SELECT COUNT(*) FROM secret_settings").fetchone()[0])
                    if self._has_table(connection, "secret_settings")
                    else 0
                )
            finally:
                connection.close()
            if integrity.lower() != "ok" or secret_count:
                raise AppError("backup_integrity_failed", "备份完整性失败或仍包含 Secret", status_code=400)
        return {"name": name, "valid": True, "manifest": manifest.model_dump(), "integrity_result": integrity}

    def restore(self, name: str) -> dict[str, Any]:
        self.verify(name)
        database = self._require_sqlite()
        path = self._path(name)
        rollback = database.with_name(
            f"{database.name}.pre-restore-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
        database.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="chan-restore-") as temporary:
            pending = Path(temporary) / "database.sqlite"
            with (
                zipfile.ZipFile(path) as archive,
                archive.open("database.sqlite") as source,
                pending.open("wb") as target,
            ):
                shutil.copyfileobj(source, target)
            if database.exists():
                shutil.copy2(database, rollback)
            try:
                os.replace(pending, database)
                connection = sqlite3.connect(database)
                try:
                    if str(connection.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
                        raise RuntimeError("restored database failed integrity check")
                finally:
                    connection.close()
            except Exception:
                if rollback.exists():
                    os.replace(rollback, database)
                raise
        return {
            "restored": name,
            "database": str(database),
            "rollback": str(rollback) if rollback.exists() else None,
        }

    def download_path(self, name: str) -> Path:
        path = self._path(name)
        if not path.is_file():
            raise AppError("backup_not_found", "备份不存在", status_code=404)
        return path

    @staticmethod
    def _has_table(connection: sqlite3.Connection, name: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _known_revisions() -> set[str]:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        return {revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()}
