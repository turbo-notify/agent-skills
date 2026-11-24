"""The skills that guide an agent through the WhatsApp tools in a chat (not the attendant).

Why they exist (spec 2026-11-21 openai-listing-discovery-and-first-run, round 3): in a local A/B
with the live tool descriptions, an agent with only the descriptions did not give the 14 day habit
after a revoked number and sent a notice to every contact without a test or a consent check; with
these skills it did. Expired message text, delivery status and quota refusals were already handled
from the descriptions alone, so there is no skill for them (do not add one without a failing case).
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import pytest

from tests.support import copy_rules

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import openai_package as pkg  # noqa: E402

NAMES = ("get-started-with-whatsapp", "send-whatsapp-messages")
TOOLS_DIR = ROOT.parent / "public-mcp-server" / "src" / "interfaces" / "mcp" / "tools"


def _text(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", NAMES)
def test_each_skill_has_frontmatter_a_use_and_stays_short(name: str) -> None:
    text = _text(name)
    lines = text.splitlines()
    assert lines[0] == "---"
    front = "\n".join(lines[1 : lines.index("---", 1)])
    assert re.search(rf"^name: {name}$", front, re.MULTILINE)
    description = re.search(r"^description: (.+)$", front, re.MULTILINE)
    assert description is not None and 40 <= len(description.group(1)) <= 1024
    assert "WhatsApp" in description.group(1) and "Use when" in description.group(1)
    assert len(lines) <= 40  # a skill a model reads in full every time stays short
    assert not re.search("[–—]", text)  # no dash as punctuation (voice rule)


def test_the_tools_the_skills_name_exist_in_the_real_catalog() -> None:
    if not TOOLS_DIR.is_dir():
        pytest.skip("the monorepo is not around")
    real = set(
        re.findall(r'name="([a-z_]+)"', "".join(p.read_text() for p in TOOLS_DIR.glob("*.py")))
    )
    cited: set[str] = set()
    for name in NAMES:
        cited |= set(re.findall(r"`([a-z]+_[a-z_]+)`", _text(name)))
    # statuses and fields the skills name are not tools
    cited -= {
        "content_expired",
        "no_main_number",
        "number_not_connected",
        "contact_id",
        "group_id",
        "number_alias",
    }
    assert {
        "list_numbers",
        "get_number",
        "connect_number",
        "send_text",
        "get_message_status",
    } <= cited
    assert cited <= real, cited - real


def test_the_copy_guards_pass_on_the_skills() -> None:
    triples = [(f"{name}/SKILL.md", "", _text(name)) for name in NAMES]
    assert copy_rules.scan(triples) == []


def test_the_facts_the_skills_state_match_the_tools() -> None:
    first = _text("get-started-with-whatsapp")
    assert "14 days" in first and "Numbers page" in first and "connect_number" in first
    assert "one hour by default" in first
    send = _text("send-whatsapp-messages")
    assert "get_message_status" in send and "quota" in send


def test_the_openai_archive_carries_the_chat_skills(
    tmp_path: Path,
) -> None:
    path, _ = pkg.build(out=tmp_path / "plugin.zip")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
    for name in NAMES:
        assert f"skills/{name}/SKILL.md" in names


def test_the_attendant_says_in_the_openai_archive_that_it_does_not_run_in_the_chat(
    tmp_path: Path,
) -> None:
    path, _ = pkg.build(out=tmp_path / "plugin.zip")
    with zipfile.ZipFile(path) as z:
        text = z.read(f"{pkg.SKILL}/SKILL.md").decode("utf-8")
    front = text.split("---")[1]
    assert "never inside a ChatGPT conversation" in front
    source = (ROOT / pkg.SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "ChatGPT" not in source  # the other marketplaces keep the generic description
