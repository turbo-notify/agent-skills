from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.support import factories as f
from tests.support.workspace import make_workspace
from tn_assist import store
from tn_assist.events import parse_events
from tn_assist.ingest import ingest
from tn_assist.paths import ContactKey
from tn_assist.queue import lease_next, pending_count
from tn_assist.state import ContactState, load_state
from tn_assist.store import read_json, read_jsonl

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def run(ws, *raw, now=T0):  # type: ignore[no-untyped-def]
    return ingest(ws, parse_events(f.payload(*raw)), now)


def transcript(ws, number: str):  # type: ignore[no-untyped-def]
    return read_jsonl(ws.contact_dir(ContactKey.parse(number)) / "transcript.jsonl")


def test_inbound_is_in_transcript_and_item_needs_hydration(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    e = f.inbound_text(preview="Olá, quero saber o pre...")
    result = run(ws, e)
    assert [i.kind for i in result.items] == ["contact"]
    assert result.items[0].hydrate is True
    (line,) = transcript(ws, f.ANA)
    assert line["id"] == e["id"] and line["role"] == "contact" and line["text"] is None
    assert line["preview"] == "Olá, quero saber o pre..."


def test_two_messages_of_one_contact_make_one_item_oldest_first(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    new = f.inbound_text(preview="segunda", created_at="2026-10-17T12:00:05Z")
    old = f.inbound_text(preview="primeira", created_at="2026-10-17T12:00:01Z")
    result = run(ws, new, old)  # the API returns newest first
    assert len(result.items) == 1 and result.items[0].event_ids == (old["id"], new["id"])
    assert [row["preview"] for row in transcript(ws, f.ANA)] == ["primeira", "segunda"]


def test_replay_after_crash_between_persist_and_cursor(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]  # RF 1
    ws = make_workspace(tmp_path)
    e = f.inbound_text()
    real = store.write_json_atomic

    def boom(path, data):  # type: ignore[no-untyped-def]
        if path.name == "cursor.json":
            raise OSError("killed")
        return real(path, data)

    monkeypatch.setattr("tn_assist.ingest.write_json_atomic", boom)
    with pytest.raises(OSError):
        run(ws, e)
    monkeypatch.undo()
    again = run(ws, e)
    assert len(transcript(ws, f.ANA)) == 1
    assert pending_count(ws) == 1
    assert again.dropped.get("drop_duplicate", 0) in (0, 1)


def test_replay_of_known_events_is_dropped(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    e = f.inbound_text()
    run(ws, e)
    second = run(ws, e)
    assert second.items == []
    assert second.dropped == {"drop_duplicate": 1}
    assert len(transcript(ws, f.ANA)) == 1


def test_cursor_is_saved_with_the_newest_event(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    new, old = f.inbound_text(), f.inbound_text()
    result = run(ws, new, old)
    assert result.new_cursor == new["id"]
    assert read_json(ws.path("cursor.json"), {})["last_event_id"] == new["id"]


def test_empty_batch_keeps_the_cursor(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    e = f.inbound_text()
    run(ws, e)
    result = run(ws, now=T0 + timedelta(seconds=20))
    assert result.new_cursor is None
    assert read_json(ws.path("cursor.json"), {})["last_event_id"] == e["id"]


def test_group_is_never_stored_or_queued(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    result = run(ws, f.inbound_group(sender=f.OWNER), f.inbound_group())
    assert result.items == [] and result.dropped == {"drop_group": 2}
    assert not (ws.root / "contacts").exists()


def test_foreign_number_counts_and_flags_the_first_notice_once(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    first = run(ws, f.inbound_text(number_id=f.OTHER_NUM))
    assert first.dropped == {"drop_foreign_number": 1} and first.first_foreign is True
    second = run(ws, f.inbound_text(number_id=f.OTHER_NUM))
    assert second.first_foreign is False
    assert read_json(ws.path("stats.json"), {})["drop_foreign_number"] == 2


def test_admin_message_goes_to_admin_thread_not_customer_memory(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    result = run(ws, f.inbound_text(sender=f.OWNER, preview="/ajuda"))
    assert [i.kind for i in result.items] == ["admin"]
    assert not (ws.root / "contacts").exists()
    assert read_jsonl(ws.path("threads", "admin", f.OWNER + ".jsonl"))[0]["text"] is None


def test_self_chat_goes_to_the_attendant_thread(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    result = run(ws, f.self_chat_native(text="/fila"))
    assert [i.kind for i in result.items] == ["attendant"]
    assert read_jsonl(ws.path("threads", "attendant", "self.jsonl"))[0]["preview"] == "/fila"


def test_takeover_pauses_that_contact_and_stores_the_human_line(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run(ws, f.inbound_text())
    result = run(ws, f.native_outbound_to_contact(preview="Já te atendo!"))
    assert result.items == []
    record = load_state(ws, ContactKey.parse(f.ANA))
    assert record.state is ContactState.PAUSED and record.human_takeover is True
    assert transcript(ws, f.ANA)[-1]["role"] == "attendant_native"


def test_gap_over_30_minutes_alarms_and_marks_transcripts(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run(ws, f.inbound_text(), now=T0)
    result = run(ws, f.inbound_text(sender=f.ANA), now=T0 + timedelta(minutes=45))
    assert result.gap_alarm is True
    kinds = [row.get("kind") for row in transcript(ws, f.ANA)]
    assert "gap" in kinds


def test_no_gap_alarm_under_30_minutes(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run(ws, f.inbound_text(), now=T0)
    assert run(ws, f.inbound_text(), now=T0 + timedelta(minutes=29)).gap_alarm is False


def test_media_only_message_is_queued_with_an_empty_marker(tmp_path: Path) -> None:  # RF 4
    ws = make_workspace(tmp_path)
    result = run(ws, f.inbound_text(content_type="image", preview=""))
    assert len(result.items) == 1
    (line,) = transcript(ws, f.ANA)
    assert line["type"] == "image" and line["preview"] == ""


def test_item_can_be_leased_after_ingest(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run(ws, f.inbound_text())
    leased = lease_next(ws, ("contact",), ttl_s=60, now=T0)
    assert leased is not None and leased.contact == f.ANA


def test_ledger_is_compacted_when_it_grows_too_large(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tn_assist import ingest as module

    ws = make_workspace(tmp_path)
    monkeypatch.setattr(module, "LEDGER_MAX", 10)
    monkeypatch.setattr(module, "LEDGER_KEEP", 4)
    for index in range(12):
        store.append_jsonl(ws.path("ledger.jsonl"), {"id": f"evt_old_{index}", "at": "x"})
    run(ws, f.inbound_text())
    ids = [row["id"] for row in read_jsonl(ws.path("ledger.jsonl"))]
    assert len(ids) == 5 and ids[0] == "evt_old_8"
