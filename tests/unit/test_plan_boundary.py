"""The platform's plan guidance is for the account owner, never for a customer of a number.

ADR 2026-11-24 D5 and spec 2026-11-24 plan-limit-guidance-for-agents, section C and A7.
The MCP server tells the owner, in an error, that something is not part of the current plan and
where to read about the plans. Nothing of that may sit in a file the virtual attendant reads or
copies: the pack of a customer, the skill, the references, the templates, the agent file.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ATTENDANT = ROOT / "skills" / "virtual-attendant"
MARKERS = (
    "plan-table-per-number-per-cycle",  # the informational link the guidance carries
    "not part of the current plan",  # the sentence the guidance opens with
    "PLANS_INFO_URL",
)


def _files() -> list[Path]:
    found = [
        p
        for p in ATTENDANT.rglob("*")
        if p.is_file()
        and p.suffix in {".md", ".json", ".txt", ".py"}
        and "__pycache__" not in p.parts
    ]
    found.append(ROOT / "agents" / "contact-attendant.md")
    return found


def test_no_file_the_attendant_reads_carries_the_plan_guidance() -> None:  # A7
    assert len(_files()) > 20  # the walk found the skill, not an empty folder
    for path in _files():
        text = path.read_text(encoding="utf-8")
        for marker in MARKERS:
            assert marker not in text, f"{path.relative_to(ROOT)} carries {marker!r}"
