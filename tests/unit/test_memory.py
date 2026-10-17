from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.support import factories as f
from tests.support.workspace import make_workspace
from tn_assist.memory import (
    Card,
    Fact,
    Policy,
    append_line,
    needs_compaction,
    purge_expired,
    read_lines,
    read_window,
    set_line_text,
    write_episode,
)
from tn_assist.paths import ContactKey
from tn_assist.store import append_jsonl, read_jsonl
from tn_assist.tokens import estimate_tokens

KEY = ContactKey.parse(f.ANA)
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def fill(ws, count: int, text: str = "x" * 400) -> None:  # type: ignore[no-untyped-def]
    for i in range(count):
        append_line(
            ws,
            KEY,
            role="contact" if i % 2 == 0 else "assistant",
            text=f"{i}:{text}",
            event_id=f"evt_{i:04d}",
            at=f"2026-10-17T10:{i % 60:02d}:00Z",
        )


def test_window_respects_budget_and_keeps_latest_turns(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    fill(ws, 100)
    window = read_window(ws, KEY, budget_tokens=500)
    assert sum(estimate_tokens(line.text) for line in window) <= 500
    assert window[-1].event_id == "evt_0099"
    assert [line.event_id for line in window] == sorted(line.event_id for line in window)


def test_full_text_replaces_the_preview_when_hydrated(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    append_jsonl(
        ws.contact_dir(KEY) / "transcript.jsonl",
        {
            "id": "evt_1",
            "msg": "msg_1",
            "at": "2026-10-17T10:00:00Z",
            "role": "contact",
            "dir": "in",
            "type": "text",
            "preview": "Olá, quero saber o pre...",
            "text": None,
        },
    )
    assert read_lines(ws, KEY)[0].text == "Olá, quero saber o pre..."
    set_line_text(ws, KEY, "evt_1", "Olá, quero saber o preço do plano anual")
    assert read_lines(ws, KEY)[0].text == "Olá, quero saber o preço do plano anual"
    assert len(read_jsonl(ws.contact_dir(KEY) / "transcript.jsonl")) == 2  # append only


def test_unreadable_text_keeps_the_preview_with_a_marker(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    append_jsonl(
        ws.contact_dir(KEY) / "transcript.jsonl",
        {
            "id": "evt_1",
            "at": "2026-10-17T10:00:00Z",
            "role": "contact",
            "dir": "in",
            "type": "text",
            "preview": "Olá, quero saber o pre...",
            "text": None,
        },
    )
    set_line_text(ws, KEY, "evt_1", None)
    line = read_lines(ws, KEY)[0]
    assert line.text == "Olá, quero saber o pre..." and line.truncated is True


def test_compaction_needed_when_window_exceeds_trigger(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    policy = Policy.load(ws)
    fill(ws, 3, "x" * 40)
    assert needs_compaction(ws, KEY, policy) is False
    fill(ws, 80)
    assert needs_compaction(ws, KEY, policy) is True


def test_episode_keeps_pointer_to_transcript_range_and_clears_the_trigger(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    policy = Policy.load(ws)
    fill(ws, 80)
    path = write_episode(ws, KEY, "Cliente perguntou preços.", covers=("evt_0000", "evt_0059"))
    text = path.read_text()
    assert "evt_0000..evt_0059" in text and "Cliente perguntou preços." in text
    window_ids = [line.event_id for line in read_window(ws, KEY, budget_tokens=100_000)]
    assert window_ids[0] == "evt_0060"
    assert needs_compaction(ws, KEY, policy) is False


def test_custom_policy_file_is_followed_unchanged(tmp_path: Path) -> None:  # AC32
    ws = make_workspace(tmp_path)
    ws.path("memory-policy.json").write_text(json.dumps({"pack_budget_contact": 1234, "custom": 1}))
    policy = Policy.load(ws)
    assert policy.pack_budget_contact == 1234 and policy.pack_budget_admin == 8000
    assert json.loads(ws.path("memory-policy.json").read_text()) == {
        "pack_budget_contact": 1234,
        "custom": 1,
    }


def test_purge_removes_only_lines_older_than_retention(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    old = (NOW - timedelta(days=91)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = (NOW - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    append_line(ws, KEY, role="contact", text="velho", event_id="evt_old", at=old)
    append_line(ws, KEY, role="contact", text="novo", event_id="evt_new", at=fresh)
    assert purge_expired(ws, NOW) == 1
    assert [line.event_id for line in read_lines(ws, KEY)] == ["evt_new"]


def test_append_line_is_idempotent_per_event(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    append_line(
        ws, KEY, role="assistant", text="oi", event_id="reply-evt_1", at="2026-10-17T10:00:00Z"
    )
    append_line(
        ws, KEY, role="assistant", text="oi", event_id="reply-evt_1", at="2026-10-17T10:00:00Z"
    )
    assert len(read_lines(ws, KEY)) == 1


def test_card_round_trip_and_precedence(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    card = Card.load(ws, KEY)
    card.merge([Fact("interest", "plano mensal", "inferred", "2026-10-17T10:00:00Z")])
    card.merge([Fact("interest", "plano anual", "admin", "2026-10-17T10:05:00Z")])
    card.merge([Fact("interest", "plano trimestral", "inferred", "2026-10-17T10:10:00Z")])
    card.save()
    again = Card.load(ws, KEY)
    assert again.get("interest").value == "plano anual" and again.get("interest").source == "admin"


def test_card_attendant_beats_inferred_and_loses_to_admin(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    card = Card.load(ws, KEY)
    card.merge([Fact("name", "Aninha", "inferred", "2026-10-17T10:00:00Z")])
    card.merge([Fact("name", "Ana Souza", "attendant", "2026-10-17T10:01:00Z")])
    assert card.get("name").value == "Ana Souza"
    card.merge([Fact("name", "Ana S.", "admin", "2026-10-17T10:02:00Z")])
    card.merge([Fact("name", "Aninha", "attendant", "2026-10-17T10:03:00Z")])
    assert card.get("name").value == "Ana S."


def test_card_render_is_markdown_with_sources(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    card = Card.load(ws, KEY)
    card.merge([Fact("name", "Ana Souza", "admin", "2026-10-17T10:00:00Z")])
    assert "Ana Souza" in card.render() and "admin" in card.render()
