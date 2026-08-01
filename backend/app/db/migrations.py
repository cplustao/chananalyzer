from __future__ import annotations

from alembic import command
from alembic.config import Config

from backend.app.core.config import PROJECT_ROOT


def upgrade_database(revision: str = "head") -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, revision)
