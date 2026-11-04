"""Builds the upload for the OpenAI plugin directory from the working tree.

The directory takes one archive: the plugin manifest (with the listing, review and publication data
under ``extensions.com.openai``), the skills, the icon and the one remote MCP server in
``mcp.json`` (the Agent Plugins shape: a root ``plugin.json`` with ``$schema``; a ``.claude-
plugin`` manifest is read as a Claude plugin and its listing block is ignored). The archive may not
carry hooks, a marketplace file for another vendor, reviewer credentials, or text that tells the
model to use another vendor's runtime. This tool leaves out what does not belong and rewrites the
few lines that name another harness, each by an EXACT replacement: when the source line changes,
the build fails and names the file, so the package cannot drift quietly from the skill. The listing
text lives in ``tools/openai_listing.json`` and is checked against the limits the portal checks
(spec D8 and D9).

Nothing is submitted from here. The upload, the identity verification, the domain token and the
review are done by a person in the portal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILL = "skills/virtual-attendant"
# The portal reads a ``.claude-plugin/plugin.json`` as a Claude plugin and ignores the OpenAI
# listing block (seen on the first draft, 2026-11-04). The Agent Plugins shape, a root
# ``plugin.json`` with ``$schema`` and ``mcp.json``, is the one that carries ``extensions``.
SOURCE_MANIFEST = ".claude-plugin/plugin.json"
MANIFEST = "plugin.json"
SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGIN_KEYS = (
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
)
LISTING = "tools/openai_listing.json"
MCP_FILE = "mcp.json"
MCP_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
# What that schema lets a server entry hold (``additionalProperties: false``): the portal found
# no server in a file that had an ``extensions`` block and no ``$schema``.
MCP_SERVER_KEYS = ("type", "url", "headers")

# Files of the working tree that go into the archive, as paths relative to the root.
INCLUDE_FILES = ("assets/icon.png", "LICENSE")
# Keys of the repository manifest that only another vendor's runtime reads.
CLAUDE_STYLE_KEYS = (
    "icon",
    "documentationUrl",
    "privacyPolicyUrl",
    "termsOfServiceUrl",
    "supportUrl",
)
# What the portal enforces on the listing (developers.openai.com/apps-sdk/deploy/submission).
NAME_MAX, SHORT_MAX, LONG_MAX, DEVELOPER_MAX = 30, 30, 4000, 80
PROMPT_MAX, PROMPTS_MAX, URL_MAX = 128, 3, 1024
IMAGE_MIN, IMAGE_MAX, IMAGE_BYTES = 48, 4096, 5 * 1024 * 1024
POSITIVE_CASES, NEGATIVE_CASES = 5, 3
URL_KEYS = ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL")
REVIEWER_KEYS = ("test_credentials", "reviewer_instructions")
DASHES = re.compile("[–—]")
INCLUDE_TREES = (SKILL,)
# Inside the skill, what belongs to another vendor's runtime.
EXCLUDE_FILES = frozenset(
    {
        f"{SKILL}/references/harness-claude-code.md",
        f"{SKILL}/references/harness-claude-desktop.md",
        f"{SKILL}/references/harness-antigravity.md",
    }
)
EXCLUDE_PARTS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})

# Words the Markdown of the archive may not contain (they name another vendor's runtime).
FOREIGN = re.compile(r"claude|anthropic|antigravity|cowork|gemini", re.IGNORECASE)
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")
# The portal does not accept these in the manifest.
FORBIDDEN_MANIFEST_KEYS = ("hooks", "apps", "mcpServers", "mcp_servers")

_FIXED_TIME = (2026, 1, 1, 0, 0, 0)


class Replacement:
    """One exact rewrite: ``old`` must appear exactly once in ``path`` or the build fails."""

    def __init__(self, path: str, old: str, new: str) -> None:
        self.path = path
        self.old = old
        self.new = new


def _row(label: str) -> str:
    """The line of the update table that starts with ``| label |``, to remove whole."""
    return f"| {label} |"


REPLACEMENTS: tuple[Replacement, ...] = (
    Replacement(
        f"{SKILL}/SKILL.md",
        "then the file for your harness: [Claude Code](references/harness-claude-code.md), "
        "[Claude Desktop or Cowork](references/harness-claude-desktop.md), "
        "[Codex](references/harness-codex.md), "
        "[Antigravity](references/harness-antigravity.md).",
        "then the file for your harness: [Codex](references/harness-codex.md).",
    ),
    Replacement(
        f"{SKILL}/references/setup.md",
        "--harness <claude-code|claude-desktop|codex|antigravity>",
        "--harness codex",
    ),
    Replacement(
        f"{SKILL}/references/setup.md",
        "(only Claude Code and Codex with the runner: in Claude Desktop, Cowork and "
        "Antigravity the command is refused and the mode stays assisted)",
        "(only with the runner: in a session the command is refused and the mode stays assisted)",
    ),
    Replacement(
        f"{SKILL}/references/setup.md",
        "Then choose how to keep it running, with the file for the harness: "
        "[Claude Code](harness-claude-code.md), "
        "[Claude Desktop or Cowork](harness-claude-desktop.md), [Codex](harness-codex.md), "
        "[Antigravity](harness-antigravity.md). Say plainly what that harness can and cannot do "
        "(for example, Claude Desktop cannot run while the app is closed).",
        "Then choose how to keep it running, with [the Codex file](harness-codex.md). "
        "Say plainly what it can and cannot do (for example, it cannot run while the computer "
        "is off).",
    ),
    Replacement(
        f"{SKILL}/references/contact-turn.md",
        "- **Claude Code in a session:** use the Agent tool with the `contact-attendant` agent "
        "that ships with this plugin. It has no tools. Pass the pack as the prompt and use its "
        "reply as the decision. Unattended runs use "
        "[harness-claude-code.md](harness-claude-code.md).\n"
        "- **Codex, Antigravity and Claude Desktop in a session:** these cannot give you a "
        "context",
        "- **Codex in a session:** this cannot give you a context",
    ),
    Replacement(
        f"{SKILL}/references/harness-codex.md",
        "Interval, logs and stopping work as in [harness-claude-code.md](harness-claude-code.md).",
        "It checks every 20 seconds (`--interval`). Logs with ids and counts (never message "
        "text) are in `<workspace>/logs/runner.jsonl`. To keep it running after you close the "
        "terminal, use what your system offers (`tmux`, `launchd`, `systemd`). To stop, press "
        "Ctrl+C: the next start catches up, as long as it is within the plan's retention "
        "(1 hour by default, so restart quickly). A second runner on the same workspace does "
        "nothing.",
    ),
)
# Rows of the update table that name another vendor, removed whole.
UPDATE_ROWS_TO_DROP = ("Claude Code", "Claude Desktop or Cowork", "Antigravity")
UPDATE_PATH = f"{SKILL}/references/update.md"


class PackageError(Exception):
    """The package cannot be built or is not acceptable."""


def version(root: Path = ROOT) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").strip()


def _wanted(rel: str) -> bool:
    parts = set(Path(rel).parts)
    return rel not in EXCLUDE_FILES and not (parts & EXCLUDE_PARTS) and not rel.endswith(".pyc")


def _collect(root: Path) -> list[str]:
    found: list[str] = []
    for rel in INCLUDE_FILES:
        if not (root / rel).is_file():
            raise PackageError(f"{rel} is missing")
        found.append(rel)
    for tree in INCLUDE_TREES:
        base = root / tree
        if not base.is_dir():
            raise PackageError(f"{tree} is missing")
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_file() and _wanted(rel):
                found.append(rel)
    return sorted(set(found))


def _rewrite(rel: str, data: bytes) -> bytes:
    """Apply the exact replacements and row removals that target ``rel``."""
    if not rel.endswith(".md"):
        return data
    text = data.decode("utf-8")
    for item in REPLACEMENTS:
        if item.path != rel:
            continue
        if text.count(item.old) != 1:
            raise PackageError(
                f"{rel}: the text to rewrite is no longer in the file exactly once "
                f"({item.old[:60]!r}...); update tools/openai_package.py"
            )
        text = text.replace(item.old, item.new)
    if rel == UPDATE_PATH:
        lines = text.split("\n")
        for label in UPDATE_ROWS_TO_DROP:
            matches = [i for i, line in enumerate(lines) if line.startswith(_row(label))]
            if len(matches) != 1:
                raise PackageError(
                    f"{rel}: the update table row {label!r} is no longer there exactly once; "
                    "update tools/openai_package.py"
                )
            del lines[matches[0]]
        text = "\n".join(lines)
    return text.encode("utf-8")


def _manifest(root: Path, demo_url: str | None) -> bytes:
    """The archive manifest in the Agent Plugins shape: the repository one, reduced to the keys
    that schema allows, with the developer name as author and the OpenAI listing block (D8)."""
    source = json.loads((root / SOURCE_MANIFEST).read_text(encoding="utf-8"))
    listing = json.loads((root / LISTING).read_text(encoding="utf-8"))
    interface = listing["interface"]
    manifest: dict[str, Any] = {"$schema": SCHEMA_URL}
    for key in AGENT_PLUGIN_KEYS:
        if key in source:
            manifest[key] = source[key]
    manifest["author"] = {"name": interface["developerName"], "url": interface["websiteURL"]}
    if demo_url:
        listing["review"]["demo_recording_url"] = demo_url
    manifest["extensions"] = {"com.openai": listing}
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _mcp_file(root: Path) -> bytes:
    """``mcp.json`` for the archive: the one server of the repository file, reduced to what the
    Agent Plugins MCP schema allows, with its ``$schema``."""
    source = json.loads((root / MCP_FILE).read_text(encoding="utf-8"))
    servers = {
        name: {key: server[key] for key in MCP_SERVER_KEYS if key in server}
        for name, server in source["mcpServers"].items()
    }
    document = {"$schema": MCP_SCHEMA_URL, "mcpServers": servers}
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def members(root: Path = ROOT, demo_url: str | None = None) -> dict[str, bytes]:
    """The archive content: relative path to bytes, already rewritten."""
    content = {rel: _rewrite(rel, (root / rel).read_bytes()) for rel in _collect(root)}
    content[MANIFEST] = _manifest(root, demo_url)
    content[MCP_FILE] = _mcp_file(root)
    return content


def _png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height of a PNG, None when the bytes are not one."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _text_fields(node: Any) -> list[str]:
    """Every string in a JSON value, to look for punctuation and forbidden keys."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in _text_fields(value)]
    if isinstance(node, list):
        return [text for value in node for text in _text_fields(value)]
    return []


def _image_problems(field: str, path: str, content: dict[str, bytes]) -> list[str]:
    member = path[2:] if path.startswith("./") else path
    data = content.get(member)
    if data is None:
        return [f"interface.{field} points to {path}, which is not in the archive"]
    size = _png_size(data)
    if size is None:
        return [f"interface.{field} ({path}) is not a PNG"]
    width, height = size
    found: list[str] = []
    if width != height:
        found.append(f"interface.{field} ({path}) is {width}x{height}, it must be square")
    if not IMAGE_MIN <= width <= IMAGE_MAX:
        found.append(
            f"interface.{field} ({path}) is {width} px, allowed {IMAGE_MIN} to {IMAGE_MAX}"
        )
    if len(data) > IMAGE_BYTES:
        found.append(f"interface.{field} ({path}) is larger than 5 MiB")
    return found


def _interface_problems(interface: dict[str, Any], content: dict[str, bytes]) -> list[str]:
    found: list[str] = []
    limits = {
        "displayName": NAME_MAX,
        "shortDescription": SHORT_MAX,
        "longDescription": LONG_MAX,
        "developerName": DEVELOPER_MAX,
    }
    for field, limit in limits.items():
        value = str(interface.get(field, "")).strip()
        if not value:
            found.append(f"interface.{field} is empty")
        elif len(value) > limit:
            found.append(f"interface.{field} has {len(value)} characters, the limit is {limit}")
    if not str(interface.get("category", "")).strip():
        found.append("interface.category is empty")
    for field in URL_KEYS:
        url = str(interface.get(field, ""))
        if not url.startswith("https://") or len(url) > URL_MAX:
            found.append(f"interface.{field} must be an HTTPS URL of at most {URL_MAX} characters")
    prompts = interface.get("defaultPrompt", [])
    if not 1 <= len(prompts) <= PROMPTS_MAX:
        found.append(f"interface.defaultPrompt needs 1 to {PROMPTS_MAX} prompts")
    for prompt in prompts:
        if len(prompt) > PROMPT_MAX:
            found.append(
                f"interface.defaultPrompt has a prompt of {len(prompt)} characters, "
                f"the limit is {PROMPT_MAX}"
            )
    for field in ("composerIcon", "logo"):
        path = str(interface.get(field, ""))
        found.extend(
            _image_problems(field, path, content) if path else [f"interface.{field} is missing"]
        )
    return found


def _review_problems(review: dict[str, Any]) -> list[str]:
    found: list[str] = []
    cases = review.get("test_cases", {})
    positive, negative = cases.get("positive", []), cases.get("negative", [])
    if len(positive) != POSITIVE_CASES:
        found.append(f"review needs {POSITIVE_CASES} positive test cases, found {len(positive)}")
    if len(negative) != NEGATIVE_CASES:
        found.append(f"review needs {NEGATIVE_CASES} negative test cases, found {len(negative)}")
    for index, case in enumerate(positive, start=1):
        for field in ("description", "prompt", "tools_triggered", "expected_behavior"):
            if not str(case.get(field, "")).strip():
                found.append(f"review positive case {index} has no {field}")
    for index, case in enumerate(negative, start=1):
        for field in ("description", "prompt"):
            if not str(case.get(field, "")).strip():
                found.append(f"review negative case {index} has no {field}")
    demo = review.get("demo_recording_url")
    if demo is not None and not str(demo).startswith("https://"):
        found.append("review.demo_recording_url must be an HTTPS URL")
    return found


def _publication_problems(publication: dict[str, Any]) -> list[str]:
    found: list[str] = []
    # An empty list removes the country restriction (worldwide); omitting the field keeps whatever
    # targeting is already saved, so the field must be present.
    if not isinstance(publication.get("countries"), list):
        found.append("publication.countries must be a list (an empty list means worldwide)")
    elif any(not re.fullmatch(r"[A-Z]{2}", str(code)) for code in publication["countries"]):
        found.append("publication.countries needs uppercase two-letter codes")
    for locale, text in publication.get("translations", {}).items():
        if len(text.get("subtitle", "")) > SHORT_MAX:
            found.append(
                f"publication.translations.{locale}.subtitle is over {SHORT_MAX} characters"
            )
        if len(text.get("description", "")) > LONG_MAX:
            found.append(
                f"publication.translations.{locale}.description is over {LONG_MAX} characters"
            )
    return found


def _mcp_problems(content: dict[str, bytes]) -> list[str]:
    """One remote server in the shape of the Agent Plugins MCP schema: public HTTPS, no header,
    no secret, no key the schema does not list (spec AC9)."""
    if MCP_FILE not in content:
        return [f"{MCP_FILE} is missing"]
    document = json.loads(content[MCP_FILE])
    found: list[str] = []
    if document.get("$schema") != MCP_SCHEMA_URL:
        found.append(f"{MCP_FILE} has no $schema of the Agent Plugins MCP format")
    for key in document:
        if key not in ("$schema", "mcpServers"):
            found.append(f"{MCP_FILE} has {key}, which the MCP schema does not allow")
    servers = document.get("mcpServers", {})
    if len(servers) != 1:
        return [*found, f"{MCP_FILE} must declare exactly one server, found {len(servers)}"]
    ((name, server),) = servers.items()
    if server.get("type") != "streamable-http":
        found.append(f"{MCP_FILE} server {name} must be of type streamable-http")
    if not str(server.get("url", "")).startswith("https://"):
        found.append(f"{MCP_FILE} server {name} must be a public HTTPS URL")
    if server.get("headers"):
        found.append(f"{MCP_FILE} server {name} has headers, which the directory does not accept")
    for key in server:
        if key not in MCP_SERVER_KEYS:
            found.append(f"{MCP_FILE} server {name} has {key}, which the MCP schema does not allow")
    if "secret" in json.dumps(server).lower():
        found.append(f"{MCP_FILE} server {name} carries a client secret")
    return found


def _listing_problems(manifest: dict[str, Any], content: dict[str, bytes]) -> list[str]:
    block = manifest.get("extensions", {}).get("com.openai")
    if not isinstance(block, dict):
        return ["the manifest has no extensions.com.openai listing"]
    found: list[str] = []
    for key in (*FORBIDDEN_MANIFEST_KEYS, *REVIEWER_KEYS):
        if key in block or key in block.get("review", {}):
            found.append(
                f"extensions.com.openai declares {key}, which the directory does not accept"
            )
    interface = block.get("interface", {})
    found.extend(_interface_problems(interface, content))
    found.extend(_review_problems(block.get("review", {})))
    found.extend(_publication_problems(block.get("publication", {})))
    author = manifest.get("author", {}).get("name")
    if author != interface.get("developerName"):
        found.append(f"author.name ({author}) and interface.developerName must be the same name")
    for key in manifest:
        if key not in AGENT_PLUGIN_KEYS:
            found.append(f"the manifest has {key}, which the Agent Plugins schema does not allow")
    if manifest.get("$schema") != SCHEMA_URL:
        found.append("the manifest has no $schema of the Agent Plugins format")
    for text in _text_fields(block):
        if DASHES.search(text):
            found.append(f"the listing text uses a dash as punctuation: {text[:50]!r}")
    return found


def missing_notes(content: dict[str, bytes]) -> list[str]:
    """What the portal will still ask for, which the build does not invent."""
    review = json.loads(content[MANIFEST])["extensions"]["com.openai"]["review"]
    return (
        []
        if review.get("demo_recording_url")
        else ["review.demo_recording_url is empty (use --demo-url)"]
    )


def problems(content: dict[str, bytes], expected_version: str) -> list[str]:
    """Why the content would be refused, empty when it is acceptable."""
    found: list[str] = []
    manifest = json.loads(content[MANIFEST])
    declared = manifest.get("version")
    if declared != expected_version:
        found.append(f"manifest version is {declared}, VERSION is {expected_version}")
    if not str(manifest.get("description", "")).strip():
        found.append("the manifest has no description")
    for key in FORBIDDEN_MANIFEST_KEYS:
        if key in manifest:
            found.append(f"the manifest declares {key}, which the directory does not accept")
    found.extend(_listing_problems(manifest, content))
    found.extend(_mcp_problems(content))
    if f"{SKILL}/SKILL.md" not in content:
        found.append("the skill is missing")
    for rel, data in content.items():
        if not rel.endswith(".md"):
            continue
        text = data.decode("utf-8")
        hit = FOREIGN.search(text)
        if hit:
            found.append(f"{rel} names another vendor's runtime ({hit.group(0)!r})")
        for target in LINK.findall(text):
            resolved = (Path(rel).parent / target).as_posix()
            if _normalise(resolved) not in content:
                found.append(f"{rel} links to {target}, which is not in the archive")
    return found


def _normalise(path: str) -> str:
    parts: list[str] = []
    for part in path.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return "/".join(parts)


def write_archive(content: dict[str, bytes], out: Path) -> str:
    """Write a deterministic ZIP and return its SHA-256."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(content):
            info = zipfile.ZipInfo(rel, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content[rel])
    return hashlib.sha256(out.read_bytes()).hexdigest()


def build(
    root: Path = ROOT, out: Path | None = None, demo_url: str | None = None
) -> tuple[Path, str]:
    ver = version(root)
    content = members(root, demo_url)
    found = problems(content, ver)
    if found:
        raise PackageError("; ".join(found))
    target = out or root / "dist" / f"turbo-notify-openai-{ver}.zip"
    return target, write_archive(content, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openai_package")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--demo-url", default=None, help="HTTPS URL of the review demo video")
    args = parser.parse_args(argv)
    try:
        path, digest = build(out=args.out, demo_url=args.demo_url)
    except PackageError as error:
        print(f"openai_package: {error}", file=sys.stderr)
        return 1
    print(path)
    print(f"sha256 {digest}")
    with zipfile.ZipFile(path) as archive:
        for note in missing_notes({name: archive.read(name) for name in archive.namelist()}):
            print(f"still missing: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
