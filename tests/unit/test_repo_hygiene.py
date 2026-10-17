"""The module is public, so its hygiene is a tested property, not a habit."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the helper itself targets 3.9
    tomllib = None

INTERNAL = (".claude/", ".private/", ".codex/", ".gemini/", ".env", "graphify-out/")
MANIFESTS = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "plugin.json",
)


def test_gitignore_excludes_internal_dirs() -> None:
    text = (ROOT / ".gitignore").read_text()
    lines = {line.strip() for line in text.splitlines()}
    for entry in INTERNAL:
        assert any(line == entry or line.startswith(entry) for line in lines), entry


def test_manifest_paths_are_not_ignored() -> None:
    for path in MANIFESTS:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-q", path],
            capture_output=True,
        )
        assert result.returncode == 1, f"{path} is ignored"


def test_no_runtime_dependencies() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    if tomllib is not None:
        data = tomllib.loads(text)
        deps = data["tool"]["poetry"].get("dependencies", {})
        assert set(deps) <= {"python"}, f"runtime dependencies found: {set(deps) - {'python'}}"
    else:  # pragma: no cover
        assert "[tool.poetry.dependencies]" in text


def test_license_is_mit() -> None:
    assert (ROOT / "LICENSE").read_text().startswith("MIT License")
