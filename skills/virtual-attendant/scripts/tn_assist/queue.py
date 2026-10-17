"""Work items waiting for a model turn.

One pending item per ``(kind, contact)``: messages that arrive while it waits are merged into it,
so a person who sends three lines in a row is answered once. A leased item belongs to one worker
until ``lease_until``; an expired lease goes back to pending (a killed runner loses nothing).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from tn_assist.paths import Workspace
from tn_assist.store import read_json, write_json_atomic
from tn_assist.timeutil import parse_iso, utc_iso

Kind = Literal["contact", "admin", "attendant", "compaction", "retro"]
_HYDRATED_KINDS = ("contact", "admin", "attendant")
MAX_EVENT_IDS = 200


@dataclass(frozen=True)
class WorkItem:
    id: str
    kind: Kind
    contact: str | None
    event_ids: tuple[str, ...]
    created_at: str
    hydrated_ids: tuple[str, ...] = ()

    @classmethod
    def new(
        cls, kind: Kind, contact: str | None, event_ids: tuple[str, ...], now: datetime
    ) -> WorkItem:
        digest = hashlib.sha1(f"{kind}|{contact}|{event_ids[0]}".encode()).hexdigest()[:12]
        return cls(
            id=f"{kind}-{digest}",
            kind=kind,
            contact=contact,
            event_ids=tuple(event_ids),
            created_at=utc_iso(now),
        )

    @property
    def hydrate(self) -> bool:
        if self.kind not in _HYDRATED_KINDS:
            return False
        return any(e not in self.hydrated_ids for e in self.event_ids)

    @property
    def key(self) -> str:
        return f"{self.kind}__{self.contact or 'self'}"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "contact": self.contact,
            "event_ids": list(self.event_ids),
            "created_at": self.created_at,
            "hydrated_ids": list(self.hydrated_ids),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WorkItem:
        return cls(
            id=data["id"],
            kind=cast(Kind, data["kind"]),
            contact=data.get("contact"),
            event_ids=tuple(data["event_ids"]),
            created_at=data["created_at"],
            hydrated_ids=tuple(data.get("hydrated_ids") or ()),
        )


def _pending_dir(ws: Workspace):  # type: ignore[no-untyped-def]
    return ws.path("queue", "pending")


def _leased_dir(ws: Workspace):  # type: ignore[no-untyped-def]
    return ws.path("queue", "leased")


def _merge(old: WorkItem, new: WorkItem) -> WorkItem:
    ids = old.event_ids + tuple(e for e in new.event_ids if e not in old.event_ids)
    return replace(old, event_ids=ids[-MAX_EVENT_IDS:])  # older lines stay in the transcript


def enqueue(ws: Workspace, item: WorkItem) -> WorkItem:
    path = _pending_dir(ws) / f"{item.key}.json"
    existing = read_json(path, None)
    merged = _merge(WorkItem.from_json(existing), item) if isinstance(existing, dict) else item
    write_json_atomic(path, merged.to_json())
    return merged


def pending_count(ws: Workspace) -> int:
    directory = _pending_dir(ws)
    return len(list(directory.glob("*.json"))) if directory.is_dir() else 0


def _leased(ws: Workspace) -> list[tuple[Any, dict[str, Any]]]:
    directory = _leased_dir(ws)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        data = read_json(path, None)
        if isinstance(data, dict):
            out.append((path, data))
    return out


def _reclaim_expired(ws: Workspace, now: datetime) -> None:
    for path, data in _leased(ws):
        if parse_iso(data["lease_until"]) <= now:
            item = WorkItem.from_json(data["item"])
            enqueue(ws, item)
            path.unlink()


def lease_next(
    ws: Workspace, kinds: tuple[str, ...], *, ttl_s: int, now: datetime
) -> WorkItem | None:
    _reclaim_expired(ws, now)
    busy = {WorkItem.from_json(d["item"]).key for _, d in _leased(ws)}
    directory = _pending_dir(ws)
    if not directory.is_dir():
        return None
    candidates = []
    for path in directory.glob("*.json"):
        data = read_json(path, None)
        if isinstance(data, dict) and data.get("kind") in kinds:
            item = WorkItem.from_json(data)
            if item.key not in busy:
                candidates.append((item.created_at, item.id, path, item))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, _, path, item = candidates[0]
    lease_until = utc_iso(now + timedelta(seconds=ttl_s))
    write_json_atomic(
        _leased_dir(ws) / f"{item.id}.json", {"item": item.to_json(), "lease_until": lease_until}
    )
    path.unlink()
    return item


def mark_hydrated(ws: Workspace, item_id: str, event_id: str) -> None:
    path = _leased_dir(ws) / f"{item_id}.json"
    data = read_json(path, None)
    if not isinstance(data, dict):
        return
    item = WorkItem.from_json(data["item"])
    if event_id not in item.hydrated_ids:
        item = replace(item, hydrated_ids=item.hydrated_ids + (event_id,))
    data["item"] = item.to_json()
    write_json_atomic(path, data)


def get_leased(ws: Workspace, item_id: str) -> WorkItem | None:
    data = read_json(_leased_dir(ws) / f"{item_id}.json", None)
    return WorkItem.from_json(data["item"]) if isinstance(data, dict) else None


def complete(ws: Workspace, item_id: str) -> None:
    path = _leased_dir(ws) / f"{item_id}.json"
    if path.exists():
        path.unlink()
