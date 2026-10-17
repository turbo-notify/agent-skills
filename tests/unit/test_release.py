from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import release  # noqa: E402


def copy_repo(tmp_path: Path) -> Path:
    for name in ("VERSION", "latest.json", "plugin.json", "CHANGELOG.md"):
        shutil.copy(ROOT / name, tmp_path / name)
    shutil.copytree(ROOT / ".claude-plugin", tmp_path / ".claude-plugin")
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    init = tmp_path / release.HELPER_INIT
    init.parent.mkdir(parents=True)
    shutil.copy(ROOT / release.HELPER_INIT, init)
    return tmp_path


def test_the_repository_is_in_agreement() -> None:  # AC2
    assert release.problems() == []


def test_a_drifting_manifest_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = copy_repo(tmp_path)
    monkeypatch.setattr(release, "ROOT", repo)
    data = json.loads((repo / "plugin.json").read_text())
    data["version"] = "9.9.9"
    (repo / "plugin.json").write_text(json.dumps(data))
    assert any("root plugin.json version is 9.9.9" in p for p in release.problems())


def test_every_kind_of_drift_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = copy_repo(tmp_path)
    monkeypatch.setattr(release, "ROOT", repo)
    plugin = json.loads((repo / ".claude-plugin/plugin.json").read_text())
    plugin.update(name="other", skills="./elsewhere", version="0.0.1")
    (repo / ".claude-plugin/plugin.json").write_text(json.dumps(plugin))
    market = json.loads((repo / ".claude-plugin/marketplace.json").read_text())
    market["plugins"][0]["source"] = "./sub"
    (repo / ".claude-plugin/marketplace.json").write_text(json.dumps(market))
    (repo / "CHANGELOG.md").write_text("# Changelog\n")
    text = " | ".join(release.problems())
    for fragment in (
        "plugin name is other",
        "skills path",
        "repository root",
        "CHANGELOG.md has no section",
        "0.0.1",
    ):
        assert fragment in text, fragment


def test_sync_is_idempotent_and_repairs_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = copy_repo(tmp_path)
    monkeypatch.setattr(release, "ROOT", repo)
    (repo / "VERSION").write_text("9.9.9\n")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## 9.9.9\n\nx\n")
    release.sync("2026-11-01")
    first = {p.name: p.read_text() for p in (repo / ".claude-plugin").iterdir()} | {
        "root": (repo / "plugin.json").read_text(),
        "latest": (repo / "latest.json").read_text(),
    }
    release.sync("2026-11-01")
    second = {p.name: p.read_text() for p in (repo / ".claude-plugin").iterdir()} | {
        "root": (repo / "plugin.json").read_text(),
        "latest": (repo / "latest.json").read_text(),
    }
    assert first == second and release.problems() == []
    assert json.loads((repo / "latest.json").read_text())["released"] == "2026-11-01"


def test_a_bad_version_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = copy_repo(tmp_path)
    monkeypatch.setattr(release, "ROOT", repo)
    (repo / "VERSION").write_text("v1\n")
    with pytest.raises(SystemExit):
        release.version()


def test_the_check_command_exits_non_zero_on_drift() -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools/release.py"), "check"], capture_output=True
    )
    assert done.returncode == 0


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_validates_the_plugin_and_marketplace() -> None:
    done = subprocess.run(
        ["claude", "plugin", "validate", str(ROOT)], capture_output=True, text=True
    )
    assert done.returncode == 0 and "Validation passed" in done.stdout


@pytest.mark.skipif(shutil.which("agy") is None, reason="agy CLI not installed")
def test_antigravity_validates_the_plugin(tmp_path: Path) -> None:
    done = subprocess.run(
        ["agy", "plugin", "validate", str(ROOT)],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    assert done.returncode == 0 and "skills" in done.stdout and "error" not in done.stdout.lower()


def test_the_manifest_files_are_not_ignored_by_git() -> None:
    for path in (
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "plugin.json",
        "agents/contact-attendant.md",
    ):
        done = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", path])
        assert done.returncode == 1, path


def test_the_customer_agent_has_no_tools() -> None:
    text = (ROOT / "agents/contact-attendant.md").read_text()
    front = text.split("---")[1]
    assert "tools: []" in front


def test_the_helper_reports_the_released_version() -> (
    None
):  # review: the notice compared it to latest.json
    from tn_assist import __version__

    assert __version__ == (ROOT / "VERSION").read_text().strip()


def test_check_catches_a_helper_that_was_not_bumped_and_sync_fixes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = copy_repo(tmp_path)
    monkeypatch.setattr(release, "ROOT", repo)
    (repo / "VERSION").write_text("9.9.9\n")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## 9.9.9\n\nx\n")
    assert any("helper __version__" in p for p in release.problems())
    release.sync(None)
    assert release.problems() == []
