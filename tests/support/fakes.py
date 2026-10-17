"""A fake Turbo Notify API and a fake harness for runner tests."""

from __future__ import annotations

import json
from typing import Any

from tests.support import factories as f
from tn_assist.events import Event, parse_events
from tn_assist.profiles import Child
from tn_assist.runner import ApiError

PEOPLE = {f.OWNER, f.CONNECTED}


class FakeApi:
    def __init__(self) -> None:
        self.stream: list[Event] = []  # oldest first
        self.texts: dict[str, str | None] = {}
        self.replies: list[tuple[str, str, str, str]] = []
        self.sends: list[tuple[str, str, str, str]] = []
        self.quotes: list[str | None] = []  # the earlier message each customer answer quoted
        self.errors: list[ApiError] = []
        self.reply_error: ApiError | None = None  # every reply fails with this, until cleared
        self.reject_quote: ApiError | None = None  # a send that carries a quote fails with this
        self.event_calls = 0

    def add(self, *raw: dict[str, Any]) -> None:
        self.stream += list(reversed(parse_events(f.payload(*raw))))  # payload is newest first

    def events(self, params: dict[str, str]) -> list[Event]:
        self.event_calls += 1
        limit = int(params["limit"])
        ids = [e.id for e in self.stream]
        newest_first = list(reversed(self.stream))
        if "ending_before" in params:
            cut = ids.index(params["ending_before"]) if params["ending_before"] in ids else -1
            pool = [e for e in newest_first if ids.index(e.id) > cut]
        elif "starting_after" in params:
            cut = ids.index(params["starting_after"])
            pool = [e for e in newest_first if ids.index(e.id) < cut]
        else:
            pool = newest_first
        return pool[:limit]

    def message_text(self, number_ref: str, message_id: str) -> str | None:
        return self.texts.get(message_id)  # unknown ids are unreadable: the preview stays

    def _maybe_fail(self) -> None:
        if self.errors:
            raise self.errors.pop(0)

    def send(self, number_ref: str, to: str, text: str, key: str, quote: str | None = None) -> None:
        """A message to one of the people (a notice) or to a customer (an answer)."""
        if quote and self.reject_quote is not None:
            raise self.reject_quote
        if to not in PEOPLE:
            if self.reply_error is not None:
                raise self.reply_error
            self._maybe_fail()
            self.replies.append((number_ref, to, text, key))
            self.quotes.append(quote)
            return
        self._maybe_fail()
        self.sends.append((number_ref, to, text, key))


class FakeHarness:
    """Records every child it is asked to start and answers from a queue or a function."""

    def __init__(self, *answers: str, rules: list[tuple[str, str]] | None = None) -> None:
        self.answers = list(answers)
        self.rules = rules or []  # (text that appears in the prompt, answer)
        self.children: list[Child] = []
        self.fail = 0

    def __call__(self, child: Child) -> str:
        self.children.append(child)
        if self.fail > 0:
            self.fail -= 1
            from tn_assist.runner import ChildFailed

            raise ChildFailed("exit 1")
        for needle, answer in self.rules:
            if needle in child.stdin.split("## New message")[-1]:
                return answer
        return self.answers.pop(0) if self.answers else json.dumps({"reply": "ok"})
