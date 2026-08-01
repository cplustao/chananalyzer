from __future__ import annotations

import subprocess
import sys
import time

from backend.app.core.config import PROJECT_ROOT
from backend.app.db.migrations import upgrade_database


def run() -> None:
    upgrade_database()
    commands = [
        [sys.executable, "-m", "backend.app.main"],
        [sys.executable, "-m", "backend.app.worker.main"],
    ]
    processes = [
        subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        for command in commands
    ]
    try:
        failed = None
        while failed is None:
            for process in processes:
                code = process.poll()
                if code is not None:
                    failed = code
                    break
            if failed is None:
                time.sleep(0.5)
        raise SystemExit(failed)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    run()
