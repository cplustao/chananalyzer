from __future__ import annotations

import argparse
import json

from backend.app.core.config import get_settings
from backend.app.services.backups import BackupService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chan-backup", description="ChanAnalyzer offline SQLite backup utility"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list")
    subcommands.add_parser("create")
    verify = subcommands.add_parser("verify")
    verify.add_argument("name")
    restore = subcommands.add_parser("restore")
    restore.add_argument("name")
    return parser


def run() -> None:
    arguments = _parser().parse_args()
    service = BackupService(get_settings())
    if arguments.command == "list":
        result = {"items": service.list()}
    elif arguments.command == "create":
        result = service.create()
    elif arguments.command == "verify":
        result = service.verify(arguments.name)
    else:
        result = service.restore(arguments.name)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run()
