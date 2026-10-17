from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from tn_assist.events import Event
from tn_assist.ingest import collect_since


def make_events(n: int) -> list[Event]:
    """Oldest first, ids sortable."""
    return [Event(f"evt_{i:05d}", "message.received", "num_x", "t", {}) for i in range(n)]


class FakeApi:
    """Mimics GET /v1/events: newest first, `limit` most recent matches, cursor semantics."""

    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.calls = 0

    def __call__(self, params: dict[str, str]) -> list[Event]:
        self.calls += 1
        limit = int(params["limit"])
        ids = [e.id for e in self.events]
        newest_first = list(reversed(self.events))
        if "ending_before" in params:
            cut = ids.index(params["ending_before"]) if params["ending_before"] in ids else -1
            pool = [e for e in newest_first if ids.index(e.id) > cut]
        elif "starting_after" in params:
            cut = ids.index(params["starting_after"])
            pool = [e for e in newest_first if ids.index(e.id) < cut]
        else:
            pool = newest_first
        return pool[:limit]


@settings(max_examples=200, deadline=None)
@given(n=st.integers(0, 250), data=st.data())
def test_never_loses_events_newer_than_the_cursor(n: int, data: Any) -> None:
    events = make_events(n)
    cursor_index = data.draw(st.integers(0, n - 1)) if n else None
    cursor = events[cursor_index].id if cursor_index is not None else None
    api = FakeApi(events)
    result = collect_since(api, cursor, limit=100)
    if cursor is None:
        assert result.events == []
        assert result.newest_id == (events[-1].id if events else None)
        return
    expected = [e.id for e in reversed(events[cursor_index + 1 :])]
    assert [e.id for e in result.events] == expected


def test_250_events_in_one_interval_are_all_returned() -> None:
    events = make_events(251)
    api = FakeApi(events)
    result = collect_since(api, events[0].id, limit=100)
    assert len(result.events) == 250
    assert api.calls == 3


def test_unknown_cursor_stops_on_a_short_page() -> None:
    events = make_events(30)
    api = FakeApi(events)
    result = collect_since(api, "evt_gone", limit=100)
    assert len(result.events) == 30
