"""Keeps the version and the manifests in agreement.

``VERSION`` is the single source. ``sync`` writes it into the three manifests and ``latest.json``;
``check`` fails (exit 1) on any disagreement, a missing changelog entry, or a manifest whose name or
skills path drifted. Run ``check`` before every release and in the tests.
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
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


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


def problems() -> list[str]:
    ver = version()
    found: list[str] = []
    plugin = _read(".claude-plugin/plugin.json")
    market = _read(".claude-plugin/marketplace.json")
    root = _read("plugin.json")
    latest = _read("latest.json")
    entry = market["plugins"][0]
    for label, value in (
        ("plugin.json version", plugin["version"]),
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
    ):
        if value != NAME:
            found.append(f"{label} is {value}, expected {NAME}")
    if plugin.get("skills") != SKILLS:
        found.append(f"plugin skills path is {plugin.get('skills')}, expected {SKILLS}")
    if entry.get("source") != "./":
        found.append("the marketplace entry must point at the repository root")
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
