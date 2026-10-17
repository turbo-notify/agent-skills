from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tn_assist.store import (
    WorkspaceLock,
    append_jsonl,
    read_json,
    read_jsonl,
    write_json_atomic,
)


def test_json_round_trip_and_modes(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "data.json"
    write_json_atomic(target, {"a": 1})
    assert read_json(target, None) == {"a": 1}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_read_json_default_when_missing(tmp_path: Path) -> None:
    assert read_json(tmp_path / "nope.json", {"x": 1}) == {"x": 1}


def test_atomic_write_survives_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "data.json"
    write_json_atomic(target, {"v": 1})

    def boom(src: str, dst: str) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_json_atomic(target, {"v": 2})
    monkeypatch.undo()
    assert read_json(target, None) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


def test_jsonl_append_and_read(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    append_jsonl(f, {"i": 1})
    append_jsonl(f, {"i": 2, "txt": "olá"})
    assert read_jsonl(f) == [{"i": 1}, {"i": 2, "txt": "olá"}]
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_jsonl_ignores_torn_last_line(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    append_jsonl(f, {"i": 1})
    with open(f, "a") as fh:
        fh.write('{"i": 2')
    assert read_jsonl(f) == [{"i": 1}]


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def lock(path: Path, clock: Clock, *, pid: int, alive: set[int]) -> WorkspaceLock:
    return WorkspaceLock(path, clock=clock, pid=pid, pid_alive=lambda p: p in alive)


def test_lock_is_exclusive(tmp_path: Path) -> None:
    c = Clock(1000.0)
    a = lock(tmp_path / "lock", c, pid=1, alive={1, 2})
    b = lock(tmp_path / "lock", c, pid=2, alive={1, 2})
    assert a.acquire(ttl_s=90) is True
    assert b.acquire(ttl_s=90) is False
    a.release()
    assert b.acquire(ttl_s=90) is True


def test_stale_lock_is_taken_over_after_ttl(tmp_path: Path) -> None:
    c = Clock(1000.0)
    a = lock(tmp_path / "lock", c, pid=1, alive={1, 2})
    b = lock(tmp_path / "lock", c, pid=2, alive={1, 2})
    assert a.acquire(ttl_s=90)
    c.now = 1000.0 + 91
    assert b.acquire(ttl_s=90) is True


def test_dead_holder_releases_immediately(tmp_path: Path) -> None:
    c = Clock(1000.0)
    a = lock(tmp_path / "lock", c, pid=1, alive={1, 2})
    b = lock(tmp_path / "lock", c, pid=2, alive={2})
    assert a.acquire(ttl_s=90)
    assert b.acquire(ttl_s=90) is True


def test_backwards_clock_does_not_steal_a_fresh_lock(tmp_path: Path) -> None:
    c = Clock(1000.0)
    a = lock(tmp_path / "lock", c, pid=1, alive={1, 2})
    b = lock(tmp_path / "lock", c, pid=2, alive={1, 2})
    assert a.acquire(ttl_s=90)
    c.now = 500.0
    assert b.acquire(ttl_s=90) is False


def test_heartbeat_keeps_the_lock_alive(tmp_path: Path) -> None:
    c = Clock(1000.0)
    a = lock(tmp_path / "lock", c, pid=1, alive={1, 2})
    b = lock(tmp_path / "lock", c, pid=2, alive={1, 2})
    assert a.acquire(ttl_s=90)
    c.now = 1080.0
    a.heartbeat()
    c.now = 1150.0
    assert b.acquire(ttl_s=90) is False


def test_release_only_removes_own_lock(tmp_path: Path) -> None:
    c = Clock(1000.0)
    a = lock(tmp_path / "lock", c, pid=1, alive={1, 2})
    b = lock(tmp_path / "lock", c, pid=2, alive={1, 2})
    assert a.acquire(ttl_s=90)
    b.release()
    assert json.loads((tmp_path / "lock").read_text())["pid"] == 1


def test_an_empty_lock_file_left_by_a_dead_holder_is_taken_over(tmp_path: Path) -> None:  # review
    path = tmp_path / "lock"
    path.write_text("")  # the holder died right after creating the file
    b = lock(path, Clock(1000.0), pid=2, alive={2})
    assert b.acquire(ttl_s=90) is True
