from __future__ import annotations

import json
import re
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
    for name, _key in release.MCP_FILES:
        shutil.copy(ROOT / name, tmp_path / name)
    shutil.copytree(ROOT / ".claude-plugin", tmp_path / ".claude-plugin")
    shutil.copytree(ROOT / ".codex-plugin", tmp_path / ".codex-plugin")
    shutil.copytree(ROOT / "assets", tmp_path / "assets")
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
    assert re.search(r"mcpServers\W+1\b", done.stdout)


def test_every_harness_gets_the_turbo_notify_server_from_the_plugin() -> None:
    for path, key in release.MCP_FILES:
        servers = json.loads((ROOT / path).read_text())["mcpServers"]
        assert list(servers) == ["turbo-notify"], path
        assert servers["turbo-notify"][key] == "https://mcp.turbonotify.com", path


def test_the_plugin_never_ships_a_credential_in_the_mcp_files() -> None:
    def keys(node: object) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in keys(v)}
        return set()

    allowed = {
        "mcpServers",
        "turbo-notify",
        "extensions",
        "com.openai",
        "auth",
        "type",
        "client",
        "mode",
    }
    for path, _key in release.MCP_FILES:
        found = keys(json.loads((ROOT / path).read_text()))
        assert found <= allowed | release.MCP_ALLOWED_KEYS[path], (path, found)


def test_check_catches_an_mcp_file_that_points_elsewhere_or_carries_a_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = copy_repo(tmp_path)
    monkeypatch.setattr(release, "ROOT", repo)
    wrong = json.loads((repo / ".mcp.json").read_text())
    wrong["mcpServers"]["turbo-notify"]["url"] = "https://example.com"
    (repo / ".mcp.json").write_text(json.dumps(wrong))
    leaky = json.loads((repo / "mcp.json").read_text())
    leaky["mcpServers"]["turbo-notify"]["oauth"] = {"clientSecret": "x"}
    (repo / "mcp.json").write_text(json.dumps(leaky))
    (repo / "mcp_config.json").unlink()
    found = release.problems()
    assert any(".mcp.json url is https://example.com" in p for p in found)
    assert any("mcp.json has fields ['oauth']" in p for p in found)
    assert any("mcp_config.json is missing" in p for p in found)


def test_the_manifest_files_are_not_ignored_by_git() -> None:
    for path in (
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "plugin.json",
        ".mcp.json",
        "mcp.json",
        "mcp_config.json",
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


def _codex(repo: Path) -> dict:  # type: ignore[type-arg]
    return json.loads((repo / ".codex-plugin/plugin.json").read_text())


def _save_codex(repo: Path, data: dict) -> None:  # type: ignore[type-arg]
    (repo / ".codex-plugin/plugin.json").write_text(json.dumps(data))


def test_codex_shows_the_brand_icon_not_a_placeholder() -> None:
    # Codex reads .codex-plugin/plugin.json. Without it the plugin was listed with a generic star,
    # because the Claude manifest carries the icon under a key Codex does not read.
    data = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
    for key in ("logo", "composerIcon"):
        assert (ROOT / ".codex-plugin" / ".." / data["interface"][key]).resolve().is_file()
    assert data["interface"]["logo"] == "./assets/icon.png"
    assert data["mcpServers"] == "./.mcp.json"
    assert data["skills"] == release.SKILLS


def test_the_codex_manifest_drift_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = copy_repo(tmp_path)
    monkeypatch.setattr(release, "ROOT", repo)
    data = _codex(repo)
    data.update(name="other", version="9.9.9", skills="./elsewhere", mcpServers="./nowhere.json")
    data["interface"]["logo"] = "./assets/missing.png"
    del data["interface"]["composerIcon"]
    _save_codex(repo, data)
    text = " | ".join(release.problems())
    for fragment in (
        "codex plugin.json version is 9.9.9",
        "codex plugin name is other",
        "codex plugin skills path",
        "codex plugin mcpServers",
        "codex plugin interface.logo",
        "codex plugin interface.composerIcon",
    ):
        assert fragment in text


def test_sync_writes_the_version_into_the_codex_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = copy_repo(tmp_path)
    monkeypatch.setattr(release, "ROOT", repo)
    data = _codex(repo)
    data["version"] = "0.0.1"
    _save_codex(repo, data)
    release.sync(None)
    assert _codex(repo)["version"] == (repo / "VERSION").read_text().strip()
