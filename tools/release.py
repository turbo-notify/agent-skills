"""Keeps the version and the manifests in agreement.

``VERSION`` is the single source. ``sync`` writes it into the five manifests and ``latest.json``;
``check`` fails (exit 1) on any disagreement, a missing changelog entry, a manifest whose name or
skills path drifted, or an MCP file that stopped pointing at the Turbo Notify server. Run ``check``
before every release and in the tests.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "turbo-notify"
SKILLS = "./skills"
CODEX_MANIFEST = ".codex-plugin/plugin.json"
CODEX_MCP = "./.mcp.json"
GEMINI_MANIFEST = "gemini-extension.json"
LOGO = "./assets/icon.png"
MIN_LOGO_SIDE = 128
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
HELPER_INIT = "skills/virtual-attendant/scripts/tn_assist/__init__.py"
MCP_URL = "https://mcp.turbonotify.com"
# One file per family of harnesses, each in the shape that family reads, the keys that hold the
# address in that shape, and the only keys the server entry may have. The plugin ships the sign-in
# connection only: a key not on this list (a header, a token, a client secret) fails the check.
MCP_FILES = (
    (
        ".mcp.json",
        ("url",),
    ),  # Claude Code, Claude Desktop and Cowork, and Codex (it reads this one too)
    (
        "mcp.json",
        ("url",),
    ),  # the Agent Plugins format (Copilot, Cursor) and the OpenAI directory upload
    # Antigravity: the documentation says ``url``, the runtime declares ``serverUrl``; both carry
    # the same address so the plugin works whichever one the installed version reads
    ("mcp_config.json", ("url", "serverUrl")),
)
MCP_ALLOWED_KEYS = {
    ".mcp.json": {"type", "url"},
    "mcp.json": {"type", "url", "extensions"},
    "mcp_config.json": {"url", "serverUrl"},
}
MCP_ALLOWED_AUTH = {"type": "oauth", "client": {"mode": "dcr"}}
# (file, pattern with the version in group 2) for the two places that carry a copy of the version
_COPIES = (
    (
        HELPER_INIT,
        re.compile(r'^(__version__\s*=\s*")([^"]+)(")', re.MULTILINE),
        "helper __version__",
    ),
    (
        "pyproject.toml",
        re.compile(r'^(version\s*=\s*")([^"]+)(")', re.MULTILINE),
        "pyproject version",
    ),
)


def _read(path: str) -> dict:  # type: ignore[type-arg]
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _write(path: str, data: dict) -> None:  # type: ignore[type-arg]
    (ROOT / path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def version() -> str:
    text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not _SEMVER.match(text):
        raise SystemExit(f"VERSION is not MAJOR.MINOR.PATCH: {text!r}")
    return text


def _mcp_problems() -> list[str]:
    found: list[str] = []
    for path, keys in MCP_FILES:
        if not (ROOT / path).is_file():
            found.append(f"{path} is missing")
            continue
        servers = _read(path).get("mcpServers", {})
        if list(servers) != [NAME]:
            found.append(f"{path} must declare only the server {NAME}, it has {sorted(servers)}")
            continue
        server = servers[NAME]
        for key in keys:
            if server.get(key) != MCP_URL:
                found.append(f"{path} {key} is {server.get(key)}, expected {MCP_URL}")
        extra = sorted(set(server) - MCP_ALLOWED_KEYS[path])
        if extra:
            found.append(
                f"{path} has fields {extra} that the plugin may not ship; it signs in with OAuth"
            )
        auth = server.get("extensions", {}).get("com.openai", {}).get("auth")
        if "extensions" in server and (
            set(server["extensions"]) != {"com.openai"} or auth != MCP_ALLOWED_AUTH
        ):
            found.append(f"{path} extensions may only ask for OAuth with dynamic registration")
    return found


def _codex_problems(codex: dict) -> list[str]:  # type: ignore[type-arg]
    """Codex reads ``.codex-plugin/plugin.json``, not the Claude manifest: without its ``interface``
    block the plugin is listed with a generic placeholder icon."""
    found: list[str] = []
    if codex.get("skills") != SKILLS:
        found.append(f"codex plugin skills path is {codex.get('skills')}, expected {SKILLS}")
    if codex.get("mcpServers") != CODEX_MCP:
        found.append(f"codex plugin mcpServers is {codex.get('mcpServers')}, expected {CODEX_MCP}")
    interface = codex.get("interface") or {}
    for key in ("logo", "composerIcon"):
        rel = interface.get(key)
        if not rel or not (ROOT / rel).is_file():
            found.append(f"codex plugin interface.{key} is {rel!r}, which is not a file")
    return found


def _png_side(path: Path) -> tuple[int, int] | None:
    """Width and height of a PNG from its header, or None when it is not a PNG."""
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _antigravity_problems(root: dict) -> list[str]:  # type: ignore[type-arg]
    """The Antigravity marketplace asks for more than the strict validator does: a display name, a
    description, an ``https`` author, up to three starter prompts and a square logo of at least
    128 pixels. ``agy plugin validate --strict`` passes with ``name`` alone, so this check carries
    them."""
    found: list[str] = []
    for key in ("displayName", "description"):
        if not root.get(key):
            found.append(
                f"root plugin.json {key} is missing (the Antigravity marketplace needs it)"
            )
    author = root.get("author")
    if not isinstance(author, dict) or not str(author.get("url", "")).startswith("https://"):
        found.append("root plugin.json author must be an object with an https url")
    prompts = root.get("suggestedPrompts")
    if not isinstance(prompts, list) or not 1 <= len(prompts) <= 3:
        found.append("root plugin.json suggestedPrompts must list one to three prompts")
    logo = root.get("logo")
    path = ROOT / logo if isinstance(logo, str) else None
    if logo != LOGO or path is None or not path.is_file():
        found.append(f"root plugin.json logo is {logo!r}, expected {LOGO} as a file")
    else:
        side = _png_side(path)
        if side is None or side[0] != side[1] or side[0] < MIN_LOGO_SIDE:
            found.append(
                f"{logo} must be a square PNG of at least {MIN_LOGO_SIDE} pixels, it is {side}"
            )
    return found


def _gemini_problems(gemini: dict) -> list[str]:  # type: ignore[type-arg]
    """The Gemini CLI gallery indexes a public repository by the ``gemini-extension.json`` at its
    root. The extension ships the sign-in connection only, on the key that CLI reads for HTTP."""
    found: list[str] = []
    if gemini.get("name") != NAME:
        found.append(f"gemini extension name is {gemini.get('name')}, expected {NAME}")
    servers = gemini.get("mcpServers", {})
    if list(servers) != [NAME]:
        found.append(
            f"gemini extension must declare only the server {NAME}, it has {sorted(servers)}"
        )
        return found
    server = servers[NAME]
    if server.get("httpUrl") != MCP_URL:
        found.append(f"gemini extension httpUrl is {server.get('httpUrl')}, expected {MCP_URL}")
    extra = sorted(set(server) - {"httpUrl"})
    if extra:
        found.append(f"gemini extension has fields {extra} that the plugin may not ship")
    return found


def problems() -> list[str]:
    ver = version()
    found: list[str] = []
    plugin = _read(".claude-plugin/plugin.json")
    market = _read(".claude-plugin/marketplace.json")
    root = _read("plugin.json")
    codex = _read(CODEX_MANIFEST)
    gemini = _read(GEMINI_MANIFEST)
    latest = _read("latest.json")
    entry = market["plugins"][0]
    for label, value in (
        ("plugin.json version", plugin["version"]),
        ("codex plugin.json version", codex.get("version")),
        ("gemini extension version", gemini.get("version")),
        ("marketplace entry version", entry["version"]),
        ("root plugin.json version", root["version"]),
        ("latest.json version", latest["version"]),
    ):
        if value != ver:
            found.append(f"{label} is {value}, VERSION is {ver}")
    for label, value in (
        ("plugin name", plugin["name"]),
        ("marketplace name", market["name"]),
        ("marketplace entry name", entry["name"]),
        ("root plugin name", root["name"]),
        ("codex plugin name", codex.get("name")),
    ):
        if value != NAME:
            found.append(f"{label} is {value}, expected {NAME}")
    if plugin.get("skills") != SKILLS:
        found.append(f"plugin skills path is {plugin.get('skills')}, expected {SKILLS}")
    found.extend(_codex_problems(codex))
    found.extend(_antigravity_problems(root))
    found.extend(_gemini_problems(gemini))
    if entry.get("source") != "./":
        found.append("the marketplace entry must point at the repository root")
    for path, pattern, label in _COPIES:
        match = pattern.search((ROOT / path).read_text(encoding="utf-8"))
        value = match.group(2) if match else "missing"
        if value != ver:
            found.append(f"{label} is {value}, VERSION is {ver}")
    found.extend(_mcp_problems())
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## {re.escape(ver)}\b", changelog, re.MULTILINE):
        found.append(f"CHANGELOG.md has no section for {ver}")
    return found


def sync(released: str | None) -> None:
    ver = version()
    plugin = _read(".claude-plugin/plugin.json")
    plugin["version"] = ver
    _write(".claude-plugin/plugin.json", plugin)
    market = _read(".claude-plugin/marketplace.json")
    market["plugins"][0]["version"] = ver
    _write(".claude-plugin/marketplace.json", market)
    root = _read("plugin.json")
    root["version"] = ver
    _write("plugin.json", root)
    codex = _read(CODEX_MANIFEST)
    codex["version"] = ver
    _write(CODEX_MANIFEST, codex)
    gemini = _read(GEMINI_MANIFEST)
    gemini["version"] = ver
    _write(GEMINI_MANIFEST, gemini)
    for path, pattern, _label in _COPIES:
        text = (ROOT / path).read_text(encoding="utf-8")
        (ROOT / path).write_text(pattern.sub(rf"\g<1>{ver}\g<3>", text, count=1), encoding="utf-8")
    latest = _read("latest.json")
    latest["version"] = ver
    if released:
        latest["released"] = released
    _write("latest.json", latest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    p = sub.add_parser("sync")
    p.add_argument("--released")
    args = parser.parse_args(argv)
    if args.command == "sync":
        sync(args.released)
        return 0
    found = problems()
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
