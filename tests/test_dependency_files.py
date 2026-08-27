from pathlib import Path

from packaging.requirements import Requirement


def test_runtime_requirements_are_parseable_and_match_project_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    requirement_lines = [
        line.strip()
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    parsed = [Requirement(line) for line in requirement_lines]
    assert len({item.name.lower() for item in parsed}) == len(parsed)

    project_text = (root / "pyproject.toml").read_text(encoding="utf-8")
    for item in parsed:
        assert f'"{item.name}' in project_text


def test_license_and_vendored_provenance_are_present() -> None:
    root = Path(__file__).resolve().parents[1]

    assert "MIT License" in (root / "LICENSE").read_text(encoding="utf-8")
    assert "Vespa314/chan.py" in (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    vendor = root / "backend" / "app" / "chan_core" / "vendor"
    assert "Copyright (c) 2022 Memos" in (vendor / "LICENSE").read_text(encoding="utf-8")
    assert "8a975ebdecee0ec86825dbc7edd60fe3cd38d07b" in (
        vendor / "UPSTREAM.md"
    ).read_text(encoding="utf-8")
