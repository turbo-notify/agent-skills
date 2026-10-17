"""AC33 and AC47, proven by structure: what the helper and the profiles can and cannot do."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tn_assist.profiles import ADMIN_READ_TOOLS, MCP_NAME, build_child
from tn_assist.sources import is_probably_write

ROOT = Path(__file__).resolve().parents[2]
HELPER = sorted((ROOT / "skills" / "virtual-attendant" / "scripts" / "tn_assist").glob("*.py"))
INSTRUCTIONS = [
    ROOT / "skills" / "virtual-attendant" / "SKILL.md",
    *sorted((ROOT / "skills" / "virtual-attendant" / "references").glob("*.md")),
    ROOT / "agents" / "contact-attendant.md",
]
ENV = {"HOME": "/home/x", "PATH": "/usr/bin"}


def _code(path: Path) -> str:
    """The file without comments and docstring-looking lines, so prose cannot trip a scan."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line.split("  # ")[0])
    return "\n".join(lines)


# --- AC33: nothing is written to the harness's own memory or instruction files ---------------

HARNESS_FILES = re.compile(r"CLAUDE\.md|AGENTS\.md|GEMINI\.md|MEMORY\.md|\.claude|\.codex|\.gemini")


@pytest.mark.parametrize(
    "path", [p for p in HELPER if p.name != "profiles.py"], ids=lambda p: p.name
)
def test_the_helper_never_names_a_harness_memory_or_instruction_file(path: Path) -> None:
    assert not HARNESS_FILES.search(_code(path)), path.name


def test_only_the_profiles_know_those_files_and_only_to_switch_them_off() -> None:
    code = _code(next(p for p in HELPER if p.name == "profiles.py"))
    assert "DISABLE_AUTO_MEMORY" in code and "DISABLE_CLAUDE_MDS" in code
    assert not re.search(r"write_text\([^)]*(CLAUDE|AGENTS|MEMORY)", code)


@pytest.mark.parametrize("path", INSTRUCTIONS, ids=lambda p: p.name)
def test_no_instruction_tells_the_model_to_use_the_harness_memory(path: Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    for phrase in ("save to memory", "remember this for later sessions", "write to memory.md"):
        assert phrase not in text, (path.name, phrase)


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("role", ["contact", "compaction", "admin", "attendant"])
def test_every_run_starts_in_an_empty_working_directory(
    tmp_path: Path, harness: str, role: str
) -> None:
    child = build_child(
        harness,  # type: ignore[arg-type]
        role,  # type: ignore[arg-type]
        "prompt",
        scratch=tmp_path,
        mcp_url="https://mcp.example",
        key="tn_live_" + "k" * 32,
        base_env=ENV,
    )
    assert child.cwd.is_dir() and list(child.cwd.iterdir()) == []
    assert child.cwd != Path.cwd() and Path.home() not in child.cwd.parents


# --- AC47: no write tool of another server, no message through another server ----------------


def test_the_only_tools_a_profile_ever_allows_are_read_tools_of_turbo_notify() -> None:
    assert ADMIN_READ_TOOLS and not any(is_probably_write(tool) for tool in ADMIN_READ_TOOLS)
    assert not {"send_text", "reply_to_message", "delete_message"} & set(ADMIN_READ_TOOLS)


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("role", ["contact", "compaction", "admin", "attendant"])
def test_a_profile_names_no_server_but_turbo_notify(
    tmp_path: Path, harness: str, role: str
) -> None:
    child = build_child(
        harness,  # type: ignore[arg-type]
        role,  # type: ignore[arg-type]
        "prompt",
        scratch=tmp_path,
        mcp_url="https://mcp.example",
        key="tn_live_" + "k" * 32,
        base_env=ENV,
    )
    joined = " ".join(child.argv)
    for server in re.findall(r"mcp__([A-Za-z0-9_-]+)__", joined):
        assert server == MCP_NAME
    for server in re.findall(r"mcp_servers\.([A-Za-z0-9_-]+)\.", joined):
        assert server == MCP_NAME
    allowed = re.findall(r"mcp__[A-Za-z0-9_-]+__([a-z_]+)", joined)
    assert not any(is_probably_write(tool) for tool in allowed)


def test_the_helper_sends_only_through_the_turbo_notify_api() -> None:
    """Every outbound call is a request to the one API base the runner is given."""
    for path in HELPER:
        code = _code(path)
        assert "smtplib" not in code and "socket.create_connection" not in code, path.name
        assert "mcp__" not in code.replace("mcp__{MCP_NAME}__", ""), path.name
    network = [p.name for p in HELPER if "urlopen" in _code(p)]
    assert sorted(network) == ["runner.py", "version.py"]  # the API, and the version file
