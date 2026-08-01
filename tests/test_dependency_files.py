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