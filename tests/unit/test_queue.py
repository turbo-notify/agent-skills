from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.support.workspace import make_workspace
from tn_assist.queue import (
    WorkItem,
    complete,
    enqueue,
    get_leased,
    lease_next,
    mark_handled,
    mark_hydrated,
    mark_rejected,
    pending_count,
    release_for_retry,
)

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def item(kind: str = "contact", contact: str | None = "5511977776666", *ids: str) -> WorkItem:
    return WorkItem.new(kind, contact, tuple(ids) or ("evt_1",), T0)  # type: ignore[arg-type]


def test_enqueue_is_idempotent_and_merges_event_ids(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    a = item("contact", "5511977776666", "evt_1")
    enqueue(ws, a)
    enqueue(ws, a)
    enqueue(ws, item("contact", "5511977776666", "evt_2"))
    assert pending_count(ws) == 1
    leased = lease_next(ws, ("contact",), ttl_s=60, now=T0)
    assert leased is not None and leased.event_ids == ("evt_1", "evt_2")
    assert leased.hydrate is True


def test_lease_is_exclusive_and_oldest_first(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    enqueue(ws, WorkItem.new("contact", "5511977776666", ("evt_1",), T0))
    enqueue(ws, WorkItem.new("contact", "5511966665555", ("evt_2",), T0 + timedelta(seconds=5)))
    first = lease_next(ws, ("contact",), ttl_s=60, now=T0)
    second = lease_next(ws, ("contact",), ttl_s=60, now=T0)
    third = lease_next(ws, ("contact",), ttl_s=60, now=T0)
    assert first is not None and first.contact == "5511977776666"
    assert second is not None and second.contact == "5511966665555"
    assert third is None


def test_kind_filter(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    enqueue(ws, item("admin", "5511955554444", "evt_1"))
    assert lease_next(ws, ("contact",), ttl_s=60, now=T0) is None
    assert lease_next(ws, ("admin",), ttl_s=60, now=T0) is not None


def test_expired_lease_returns_to_pending(tmp_path: Path) -> None:  # RF 5, killed runner
    ws = make_workspace(tmp_path)
    enqueue(ws, item())
    assert lease_next(ws, ("contact",), ttl_s=60, now=T0) is not None
    assert lease_next(ws, ("contact",), ttl_s=60, now=T0 + timedelta(seconds=30)) is None
    again = lease_next(ws, ("contact",), ttl_s=60, now=T0 + timedelta(seconds=61))
    assert again is not None


def test_complete_removes_the_lease(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    enqueue(ws, item())
    leased = lease_next(ws, ("contact",), ttl_s=60, now=T0)
    assert leased is not None
    complete(ws, leased.id)
    assert lease_next(ws, ("contact",), ttl_s=60, now=T0 + timedelta(hours=1)) is None


def test_event_ids_per_item_are_capped(tmp_path: Path) -> None:  # security review
    from tn_assist.queue import MAX_EVENT_IDS

    ws = make_workspace(tmp_path)
    for start in range(0, 600, 100):
        ids = tuple(f"evt_{i:05d}" for i in range(start, start + 100))
        enqueue(ws, WorkItem.new("contact", "5511977776666", ids, T0))
    leased = lease_next(ws, ("contact",), ttl_s=60, now=T0)
    assert leased is not None and len(leased.event_ids) == MAX_EVENT_IDS
    assert leased.event_ids[-1] == "evt_00599"


def test_a_reclaimed_lease_keeps_order_and_what_was_already_done(tmp_path: Path) -> None:  # review
    ws = make_workspace(tmp_path)
    enqueue(ws, item("admin", None, "e1", "e2"))
    leased = lease_next(ws, ("admin",), ttl_s=60, now=T0)
    assert leased is not None
    mark_handled(ws, leased.id, "e1")
    mark_hydrated(ws, leased.id, "e1")
    enqueue(ws, item("admin", None, "e3"))  # a new message while the first batch is leased
    later = T0 + timedelta(seconds=120)
    again = lease_next(ws, ("admin",), ttl_s=60, now=later)
    assert again is not None
    assert again.event_ids == ("e1", "e2", "e3")  # oldest first, so the newest is still last
    assert "e1" in again.handled_ids and "e1" in again.hydrated_ids  # a command is not run twice


def test_a_refused_answer_is_counted_and_the_count_survives_the_wait(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    enqueue(ws, item("contact", "5511977776666", "e1"))
    leased = lease_next(ws, ("contact",), ttl_s=900, now=T0)
    assert leased is not None and leased.rejected == 0
    assert mark_rejected(ws, leased.id) == 1
    release_for_retry(ws, leased.id, T0, 1)
    enqueue(ws, item("contact", "5511977776666", "e2"))  # the customer writes again meanwhile
    again = lease_next(ws, ("contact",), ttl_s=900, now=T0 + timedelta(seconds=5))
    assert again is not None and again.event_ids == ("e1", "e2")
    assert again.rejected == 1 and again.attempts == 2  # a refusal is not an extra attempt
    assert mark_rejected(ws, again.id) == 2


def test_an_item_written_before_refusals_were_counted_starts_at_zero() -> None:
    old = {"id": "contact-1", "kind": "contact", "contact": "5511977776666", "event_ids": ["e1"],
           "created_at": "2026-10-17T12:00:00Z", "attempts": 2}  # fmt: skip
    assert WorkItem.from_json(old).rejected == 0


def test_counting_a_refusal_for_an_item_that_is_gone_counts_nothing(tmp_path: Path) -> None:
    assert mark_rejected(make_workspace(tmp_path), "contact-missing") == 0


def test_the_rule_a_refused_answer_broke_travels_with_the_item(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    enqueue(ws, item("contact", "5511977776666", "e1"))
    leased = lease_next(ws, ("contact",), ttl_s=900, now=T0)
    assert leased is not None and leased.refusal == ""
    assert mark_rejected(ws, leased.id, "it was not valid JSON") == 1
    release_for_retry(ws, leased.id, T0, 1)
    enqueue(ws, item("contact", "5511977776666", "e2"))  # the customer writes again meanwhile
    again = lease_next(ws, ("contact",), ttl_s=900, now=T0 + timedelta(seconds=5))
    assert again is not None and again.refusal == "it was not valid JSON"
    # a later refusal replaces the rule; completing the item drops it for good
    assert mark_rejected(ws, again.id, '"facts" must be a list') == 2
    reread = get_leased(ws, again.id)
    assert reread is not None and reread.refusal == '"facts" must be a list'
    complete(ws, again.id)
    enqueue(ws, item("contact", "5511977776666", "e3"))
    fresh = lease_next(ws, ("contact",), ttl_s=900, now=T0 + timedelta(minutes=2))
    assert fresh is not None and fresh.refusal == "" and fresh.rejected == 0


def test_an_item_written_before_the_rule_was_kept_has_none() -> None:
    old = {"id": "contact-1", "kind": "contact", "contact": "5511977776666", "event_ids": ["e1"],
           "created_at": "2026-10-17T12:00:00Z", "rejected": 1}  # fmt: skip
    assert WorkItem.from_json(old).refusal == ""
