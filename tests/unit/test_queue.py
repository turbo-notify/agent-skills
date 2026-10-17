from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.support.workspace import make_workspace
from tn_assist.queue import WorkItem, complete, enqueue, lease_next, pending_count

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
