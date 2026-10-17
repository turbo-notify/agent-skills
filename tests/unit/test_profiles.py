from __future__ import annotations

from pathlib import Path

import pytest

from tn_assist.profiles import (
    ADMIN_READ_TOOLS,
    KEY_ENV,
    UnsupportedHarness,
    build_child,
)

KEY = "tn_live_" + "k" * 32
ENV = {
    "HOME": "/home/x",
    "PATH": "/usr/bin",
    "ANTHROPIC_API_KEY": "model-key",
    "TURBO_NOTIFY_API_KEY": KEY,
    "SECRET_OTHER": "nope",
    "CLAUDE_CONFIG_DIR": "/home/x/.claude",
}
WRITE_TOOLS = (
    "send_text",
    "send_media",
    "reply_to_message",
    "delete_message",
    "connect_number",
    "disconnect_number",
    "refresh_contact",
    "edit_text_message",
    "react_to_message",
)


def child(harness: str, role: str, tmp_path: Path, **kw):  # type: ignore[no-untyped-def]
    return build_child(
        harness,
        role,
        "PROMPT",
        scratch=tmp_path,
        mcp_url="https://mcp.example.test",
        key=kw.pop("key", KEY),
        base_env=kw.pop("base_env", ENV),
        **kw,
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("role", ["contact", "compaction"])
def test_claude_contact_has_no_tools_no_servers_and_no_key(tmp_path: Path, role: str) -> None:
    c = child("claude-code", role, tmp_path)
    assert c.argv[:2] == ["claude", "-p"]
    assert c.argv[c.argv.index("--tools") : c.argv.index("--tools") + 2] == ["--tools", ""]
    assert "--allowedTools" not in c.argv and "--strict-mcp-config" in c.argv
    assert '"mcpServers": {}' in (tmp_path / "mcp.json").read_text()
    assert KEY_ENV not in c.env and KEY not in " ".join(c.argv) and KEY not in c.env.values()
    assert "SECRET_OTHER" not in c.env and c.env["ANTHROPIC_API_KEY"] == "model-key"
    assert (
        c.env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
        and c.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    )
    assert "--setting-sources" in c.argv and c.stdin == "PROMPT"
    assert c.cwd == tmp_path / "cwd" and c.cwd.is_dir()


@pytest.mark.parametrize("role", ["admin", "attendant"])
def test_claude_admin_has_only_read_tools_and_the_key_in_one_variable(
    tmp_path: Path, role: str
) -> None:
    c = child("claude-code", role, tmp_path)
    allowed = c.argv[c.argv.index("--allowedTools") + 1 :]
    assert sorted(allowed[: len(ADMIN_READ_TOOLS)]) == sorted(
        f"mcp__turbo-notify__{t}" for t in ADMIN_READ_TOOLS
    )
    for tool in WRITE_TOOLS:
        assert not any(tool in a for a in allowed)
    assert c.env[KEY_ENV] == KEY
    config = (tmp_path / "mcp.json").read_text()
    assert "${" + KEY_ENV + "}" in config and KEY not in config  # the file names the variable only
    assert KEY not in " ".join(c.argv)


def test_codex_contact_has_its_own_home_with_only_the_login(tmp_path: Path) -> None:
    real = tmp_path / "real-codex"
    real.mkdir()
    (real / "auth.json").write_text("{}")
    (real / "AGENTS.md").write_text("owner's personal instructions")
    scratch = tmp_path / "scratch"
    c = child("codex", "contact", scratch, base_env={**ENV, "CODEX_HOME": str(real)})
    home = Path(c.env["CODEX_HOME"])
    assert home != real and home.parent == scratch
    assert sorted(p.name for p in home.iterdir()) == ["auth.json"]
    assert (home / "auth.json").is_symlink()
    assert not (home / "AGENTS.md").exists()
    for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "read-only"):
        assert flag in c.argv
    for feature in ("shell_tool", "unified_exec", "apps", "code_mode_host", "plugins", "hooks"):
        i = c.argv.index(feature)
        assert c.argv[i - 1] == "--disable"
    assert "mcp_servers" not in " ".join(c.argv) and KEY_ENV not in c.env
    assert c.output_file == scratch / "answer.txt" and "-o" in c.argv and c.stdin == "PROMPT"


def test_codex_admin_gets_the_read_tools_and_the_key_variable_name(tmp_path: Path) -> None:
    c = child("codex", "admin", tmp_path)
    joined = " ".join(c.argv)
    assert 'bearer_token_env_var="TURBO_NOTIFY_API_KEY"' in joined
    assert "code_mode_host" not in c.argv  # MCP tools are exposed through it
    for feature in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "multi_agent"):
        assert c.argv[c.argv.index(feature) - 1] == "--disable"
    assert (
        "enabled_tools=" in joined
        and "send_text" not in joined
        and "reply_to_message" not in joined
    )
    assert c.env[KEY_ENV] == KEY and KEY not in joined


def test_the_home_default_is_used_when_codex_home_is_unset(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}")
    c = child("codex", "contact", tmp_path / "s", base_env={"HOME": str(home), "PATH": "/usr/bin"})
    assert (Path(c.env["CODEX_HOME"]) / "auth.json").resolve() == (
        home / ".codex" / "auth.json"
    ).resolve()


@pytest.mark.parametrize("harness", ["antigravity", "gemini", "claude-desktop", "unknown"])
def test_other_harnesses_are_refused(tmp_path: Path, harness: str) -> None:
    with pytest.raises(UnsupportedHarness):
        child(harness, "contact", tmp_path)


def test_an_admin_child_without_a_key_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        child("claude-code", "admin", tmp_path, key=None)


def test_model_choice_is_passed_through(tmp_path: Path) -> None:
    assert "--model" in child("claude-code", "contact", tmp_path / "a", model="m1").argv
    assert "-m" in child("codex", "contact", tmp_path / "b", model="m1").argv
