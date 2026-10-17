from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.support.workspace import make_workspace
from tn_assist.version import UpdateNotice, check, parse_version, update_hint

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
LATEST = {
    "version": "0.3.0",
    "released": "2026-10-20",
    "min_workspace_schema": 1,
    "notes_url": "https://example.test/notes",
}


def fetcher(payload: Any, calls: list[int] | None = None):  # type: ignore[no-untyped-def]
    def fetch() -> Any:
        if calls is not None:
            calls.append(1)
        if isinstance(payload, Exception):
            raise payload
        return payload

    return fetch


def test_parse_version() -> None:
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("v0.10.0") == (0, 10, 0)
    for bad in ("", "1.2", "a.b.c", "1.2.3.4", "1.2.-3"):
        with pytest.raises(ValueError):
            parse_version(bad)


def test_newer_latest_produces_a_notice_with_versions_and_the_harness_command(
    tmp_path: Path,
) -> None:  # AC5
    ws = make_workspace(tmp_path)
    notice = check(ws, "0.1.0", fetcher(LATEST), NOW, harness="codex")
    assert isinstance(notice, UpdateNotice)
    assert (notice.installed, notice.latest) == ("0.1.0", "0.3.0")
    assert "codex plugin marketplace upgrade" in notice.command
    assert "codex plugin add turbo-notify@turbo-notify" in notice.command
    assert notice.notes_url == "https://example.test/notes"


def test_same_or_older_version_gives_no_notice(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    assert check(ws, "0.3.0", fetcher(LATEST), NOW) is None
    assert check(ws, "0.4.0", fetcher(LATEST), NOW + timedelta(hours=2)) is None


def test_unreachable_or_garbage_check_is_silent(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    assert check(ws, "0.1.0", fetcher(OSError("offline")), NOW) is None
    assert check(ws, "0.1.0", fetcher({"nope": 1}), NOW + timedelta(hours=2)) is None
    assert check(ws, "0.1.0", fetcher("junk"), NOW + timedelta(hours=4)) is None
    assert (
        check(ws, "0.1.0", fetcher({"version": "not-a-version"}), NOW + timedelta(hours=6)) is None
    )


def test_the_answer_is_cached_for_one_hour(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    calls: list[int] = []
    check(ws, "0.1.0", fetcher(LATEST, calls), NOW)
    check(ws, "0.1.0", fetcher(LATEST, calls), NOW + timedelta(minutes=59))
    assert len(calls) == 1
    check(ws, "0.1.0", fetcher(LATEST, calls), NOW + timedelta(minutes=61))
    assert len(calls) == 2


def test_a_cached_notice_is_still_returned(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    check(ws, "0.1.0", fetcher(LATEST), NOW)
    again = check(ws, "0.1.0", fetcher(OSError("offline")), NOW + timedelta(minutes=10))
    assert again is not None and again.latest == "0.3.0"


def test_a_failed_check_is_retried_after_a_short_while_not_an_hour(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    calls: list[int] = []
    check(ws, "0.1.0", fetcher(OSError("offline"), calls), NOW)
    check(ws, "0.1.0", fetcher(OSError("offline"), calls), NOW + timedelta(minutes=2))
    assert len(calls) == 1
    check(ws, "0.1.0", fetcher(OSError("offline"), calls), NOW + timedelta(minutes=12))
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        (
            "claude-code",
            [
                "claude plugin marketplace update turbo-notify",
                "claude plugin update turbo-notify@turbo-notify",
            ],
        ),
        (
            "codex",
            ["codex plugin marketplace upgrade", "codex plugin add turbo-notify@turbo-notify"],
        ),
        ("antigravity", ["agy plugin install https://github.com/turbo-notify/agent-skills"]),
    ],
)
def test_update_commands_per_harness(harness: str, expected: list[str]) -> None:
    hint = update_hint(harness, "en")  # type: ignore[arg-type]
    for fragment in expected:
        assert fragment in hint


def test_claude_desktop_hint_is_a_description_not_a_command() -> None:
    assert "marketplace" in update_hint("claude-desktop", "en").lower()


def test_unknown_harness_lists_every_way(tmp_path: Path) -> None:
    hint = update_hint(None, "pt")
    assert (
        "claude plugin update" in hint
        and "codex plugin add" in hint
        and "agy plugin install" in hint
    )


def test_hints_follow_the_copy_rules() -> None:
    from tests.support import copy_rules

    triples = [
        (h, lang, update_hint(h, lang))  # type: ignore[arg-type]
        for h in ("claude-code", "claude-desktop", "codex", "antigravity", None)
        for lang in ("pt", "en")
    ]
    assert copy_rules.scan(triples) == []
