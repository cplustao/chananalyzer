from __future__ import annotations

import json
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT
from backend.app.main import app


def main(target: Path | None = None) -> Path:
    output = target or PROJECT_ROOT / "frontend" / "openapi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    return output


if __name__ == "__main__":
    main()