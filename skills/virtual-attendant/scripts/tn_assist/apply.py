"""What a model decision is allowed to cause.

A model returns text; this module turns it into actions that trusted code carries out. It parses
strictly (unknown keys are refused), keeps the conversation state machine, enforces the caps and
the hand-off rules, and never lets a decision skip the guard that runs before anything is sent.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Union

from tn_assist.config import load_config
from tn_assist.egress import check_egress
from tn_assist.i18n import t
from tn_assist.memory import (
    AUTO_RESUME_MIN_MINUTES,
    Card,
    Fact,
    Policy,
    append_line,
    read_lines,
    write_episode,
)
from tn_assist.paths import ContactKey, InvalidContactKey, Workspace
from tn_assist.queue import WorkItem, drop_outbox_for
from tn_assist.sanitize import clean_fact_value, clean_summary
from tn_assist.state import ContactState, load_state, save_state
from tn_assist.store import read_json, write_json_atomic
from tn_assist.timeutil import parse_iso, utc_iso

Role = Literal["contact", "admin", "attendant"]
Mode = Literal["auto", "assist"]
MAX_REPLY_CHARS = 4000
_FACT_KEY = re.compile(r"^[a-z0-9_]{1,40}$")
_LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
_CONTACT_KEYS = frozenset({"reply", "facts", "escalate", "language", "state", "quote"})
_MESSAGE_ID = re.compile(r"^msg_[0-9a-f]{32}$")
_CONSOLE_KEYS = frozenset({"reply", "command"})
_URGENCY = ("low", "normal", "high")
LOOP_MIN_CHARS = 12
LOOP_SIMILARITY = 0.95
LOOP_WINDOW = timedelta(minutes=10)


class InvalidDecision(ValueError):
    """The model's answer is not an acceptable decision."""


@dataclass(frozen=True)
class Escalation:
    reason: str
    summary: str
    urgency: str


@dataclass(frozen=True)
class DecisionFact:
    key: str
    value: str


@dataclass(frozen=True)
class Decision:
    reply: str | None = None
    facts: tuple[DecisionFact, ...] = ()
    escalate: Escalation | None = None
    language: str | None = None
    state: str | None = None
    command: str | None = None
    quote: str | None = None


@dataclass(frozen=True)
class Reply:
    """A normal message to the customer. ``quote_message_id`` is an EARLIER message, if any."""

    contact: str
    quote_message_id: str | None
    text: str
    idempotency_key: str


@dataclass(frozen=True)
class Draft:
    number: int
    contact: str
    text: str
    event_id: str
    quote_message_id: str | None


@dataclass(frozen=True)
class NotifyAdmins:
    text: str


@dataclass(frozen=True)
class NotifySelfChat:
    text: str


@dataclass(frozen=True)
class Say:
    """An answer to one person who is using the console. ``to`` is a number or ``"self"``."""

    to: str
    text: str


Action = Union[Reply, Draft, NotifyAdmins, NotifySelfChat, Say]
Guard = Callable[[Action], Action]


def _extract_json(raw: str) -> Any:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise InvalidDecision("no JSON object")
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise InvalidDecision("not valid JSON") from error


def _text(value: Any, name: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise InvalidDecision(f"{name} must be a string up to {limit} characters")
    return value


def parse_decision(role: Role, raw: str) -> Decision:
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise InvalidDecision("decision must be an object")
    allowed = _CONTACT_KEYS if role == "contact" else _CONSOLE_KEYS
    unknown = set(data) - allowed
    if unknown:
        raise InvalidDecision(f"unknown keys: {sorted(unknown)}")
    reply = _text(data.get("reply"), "reply", MAX_REPLY_CHARS)
    if role != "contact":
        return Decision(reply=reply, command=_text(data.get("command"), "command", 500))
    facts_raw = data.get("facts", [])
    if not isinstance(facts_raw, list):
        raise InvalidDecision("facts must be a list")
    facts = []
    for entry in facts_raw:
        if not isinstance(entry, dict) or set(entry) != {"key", "value"}:
            raise InvalidDecision("each fact needs a key and a value")
        key, value = entry["key"], entry["value"]
        if not isinstance(key, str) or not _FACT_KEY.match(key) or not isinstance(value, str):
            raise InvalidDecision("bad fact")
        if key.startswith("note_"):
            continue  # notes are the team's: a customer's words must not pose as one
        facts.append(DecisionFact(key, clean_fact_value(value)))
    escalation = None
    if data.get("escalate") is not None:
        raw_esc = data["escalate"]
        if not isinstance(raw_esc, dict) or set(raw_esc) != {"reason", "summary", "urgency"}:
            raise InvalidDecision("bad escalation")
        urgency = raw_esc["urgency"]
        if urgency not in _URGENCY:
            raise InvalidDecision("bad urgency")
        escalation = Escalation(
            _text(raw_esc["reason"], "reason", 300) or "",
            _text(raw_esc["summary"], "summary", 600) or "",
            urgency,
        )
    language = data.get("language")
    if language is not None and (not isinstance(language, str) or not _LANG.match(language)):
        raise InvalidDecision("bad language")
    state = data.get("state")
    if state not in (None, "do_not_contact"):
        raise InvalidDecision("state can only be do_not_contact")
    quote = data.get("quote")
    if not isinstance(quote, str) or not _MESSAGE_ID.match(quote):
        quote = None  # a quote is a nicety: a bad id means a plain message, not a failed turn
    return Decision(reply, tuple(facts), escalation, language, state, quote=quote)


@dataclass(frozen=True)
class Compaction:
    summary: str
    facts: tuple[DecisionFact, ...]


def parse_compaction(raw: str) -> Compaction:
    data = _extract_json(raw)
    if not isinstance(data, dict) or set(data) - {"summary", "facts"} or "summary" not in data:
        raise InvalidDecision("a compaction has a summary and optional facts")
    summary = _text(data["summary"], "summary", 2000)
    if not summary or not summary.strip():
        raise InvalidDecision("empty summary")
    facts_raw = data.get("facts", [])
    if not isinstance(facts_raw, list):
        raise InvalidDecision("facts must be a list")
    facts = []
    for entry in facts_raw:
        if not isinstance(entry, dict) or set(entry) != {"key", "value"}:
            raise InvalidDecision("each fact needs a key and a value")
        key, value = entry["key"], entry["value"]
        if not isinstance(key, str) or not _FACT_KEY.match(key) or not isinstance(value, str):
            raise InvalidDecision("bad fact")
        if key.startswith("note_"):
            continue
        facts.append(DecisionFact(key, clean_fact_value(value)))
    cleaned = clean_summary(summary)
    if not cleaned:
        raise InvalidDecision("empty summary")
    return Compaction(cleaned, tuple(facts))


def apply_compaction(ws: Workspace, item: WorkItem, compaction: Compaction, now: datetime) -> Path:
    assert item.contact is not None
    key = ContactKey.parse(item.contact)
    path = write_episode(
        ws, key, compaction.summary, covers=(item.event_ids[0], item.event_ids[-1]), now=now
    )
    if compaction.facts:
        card = Card.load(ws, key)
        card.merge([Fact(f.key, f.value, "inferred", utc_iso(now)) for f in compaction.facts])
        card.save()
    return path


# --- drafts -----------------------------------------------------------------------------


def _drafts_path(ws: Workspace) -> Path:
    return ws.path("drafts.json")


def list_drafts(ws: Workspace) -> list[dict[str, Any]]:
    data = read_json(_drafts_path(ws), {})
    return list(data.get("drafts", [])) if isinstance(data, dict) else []


def _add_draft(
    ws: Workspace,
    contact: str,
    text: str,
    event_id: str,
    quote_message_id: str | None,
    now: datetime,
    *,
    blocked: bool = False,
) -> Draft:
    data = read_json(_drafts_path(ws), {})
    data = data if isinstance(data, dict) else {}
    number = int(data.get("next", 1))
    drafts = list(data.get("drafts", []))
    drafts.append(
        {
            "n": number,
            "contact": contact,
            "text": text,
            "event_id": event_id,
            "quote_message_id": quote_message_id,
            "created_at": utc_iso(now),
            "blocked": blocked,
        }
    )
    write_json_atomic(_drafts_path(ws), {"next": number + 1, "drafts": drafts})
    return Draft(number, contact, text, event_id, quote_message_id)


def pop_draft(ws: Workspace, number: int) -> dict[str, Any] | None:
    data = read_json(_drafts_path(ws), {})
    drafts = list(data.get("drafts", [])) if isinstance(data, dict) else []
    found: dict[str, Any] | None = next((d for d in drafts if d["n"] == number), None)
    if found is None:
        return None
    rest = [d for d in drafts if d["n"] != number]
    write_json_atomic(_drafts_path(ws), {"next": data.get("next", number + 1), "drafts": rest})
    return found


# --- escalations ------------------------------------------------------------------------


def _escalation_path(ws: Workspace, key: ContactKey) -> Path:
    return ws.path("escalations", f"{key}.json")


def open_escalations(ws: Workspace) -> list[dict[str, Any]]:
    directory = ws.path("escalations")
    if not directory.is_dir():
        return []
    rows = [read_json(p, None) for p in sorted(directory.glob("*.json"))]
    return sorted([r for r in rows if isinstance(r, dict)], key=lambda r: r["opened_at"])


def _lang(ws: Workspace) -> Literal["pt", "en"]:
    return "en" if load_config(ws).get("lang") == "en" else "pt"


def _display_name(ws: Workspace, key: ContactKey) -> str:
    fact = Card.load(ws, key).get("name")
    return fact.value if fact else str(key)


def _last_inbound(ws: Workspace, key: ContactKey) -> str:
    lines = [ln for ln in read_lines(ws, key) if ln.role == "contact" and ln.kind == "message"]
    return (lines[-1].text if lines else "")[:200].replace("\n", " ")


def _notices(text: str) -> list[Action]:
    return [NotifyAdmins(text), NotifySelfChat(text)]


# --- state machine ----------------------------------------------------------------------


class OptedOut(Exception):
    """The customer asked not to be contacted; only an explicit override may change that."""


def drop_drafts_for(ws: Workspace, contact: str) -> None:
    data = read_json(_drafts_path(ws), {})
    if isinstance(data, dict) and data.get("drafts"):
        data["drafts"] = [d for d in data["drafts"] if d.get("contact") != contact]
        write_json_atomic(_drafts_path(ws), data)


def resume_contact(
    ws: Workspace, key: ContactKey, *, by: str, now: datetime, override_opt_out: bool = False
) -> None:
    if load_state(ws, key).state is ContactState.DO_NOT_CONTACT and not override_opt_out:
        raise OptedOut(str(key))
    record = replace(
        load_state(ws, key),
        state=ContactState.ACTIVE,
        reason=None,
        human_takeover=False,
        since=utc_iso(now),
    )
    save_state(ws, key, record)
    path = _escalation_path(ws, key)
    if path.exists():
        path.unlink()


def _recent(stamps: list[str], now: datetime, window: timedelta) -> int:
    return sum(1 for s in stamps if now - parse_iso(s) < window)


def _looks_like_loop(ws: Workspace, key: ContactKey) -> bool:
    lines = [ln for ln in read_lines(ws, key) if ln.role == "contact" and ln.kind == "message"]
    if len(lines) < 2:
        return False
    a, b = lines[-2], lines[-1]
    norm = [re.sub(r"\W+", " ", ln.text.lower()).strip() for ln in (a, b)]
    if min(len(n) for n in norm) < LOOP_MIN_CHARS:
        return False
    try:
        close = abs(parse_iso(b.at) - parse_iso(a.at)) <= LOOP_WINDOW
    except ValueError:
        close = True
    return close and difflib.SequenceMatcher(None, norm[0], norm[1]).ratio() >= LOOP_SIMILARITY


def _pause(
    ws: Workspace, key: ContactKey, reason_key: str, now: datetime, record: Any
) -> list[Action]:
    lang = _lang(ws)
    save_state(
        ws, key, replace(record, state=ContactState.PAUSED, reason=reason_key, since=utc_iso(now))
    )
    return _notices(
        t("contact_paused", lang, number=str(key), reason=t(f"reason_{reason_key}", lang))
    )


def _open_escalation(
    ws: Workspace, key: ContactKey, esc: Escalation, now: datetime, record: Any
) -> list[Action]:
    lang = _lang(ws)
    save_state(
        ws, key, replace(record, state=ContactState.HANDOFF, reason="escalated", since=utc_iso(now))
    )
    write_json_atomic(
        _escalation_path(ws, key),
        {
            "contact": str(key),
            "reason": esc.reason,
            "summary": esc.summary,
            "urgency": esc.urgency,
            "opened_at": utc_iso(now),
            "last_reminder_at": None,
        },
    )
    return _notices(
        t(
            "escalation_notice",
            lang,
            name=_display_name(ws, key),
            number=str(key),
            reason=esc.reason,
            summary=esc.summary,
            last=_last_inbound(ws, key),
        )
    )


def due_auto_resumes(ws: Workspace, now: datetime) -> list[Action]:
    """Give a conversation back when no person has written for ``auto_resume_after_minutes``.

    Only a human takeover is handed back: a pause by an administrator, an escalation waiting for a
    person and a customer who asked to stop all stay as they are. ``since`` is the moment of the
    last message a person wrote, so a conversation that is still going is never handed back.
    """
    minutes = Policy.load(ws).auto_resume_after_minutes
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, int)
        or minutes < AUTO_RESUME_MIN_MINUTES
    ):
        return []
    lang = _lang(ws)
    actions: list[Action] = []
    root = ws.root / "contacts"
    for directory in sorted(root.iterdir()) if root.is_dir() else []:
        try:
            key = ContactKey.parse(directory.name)
        except InvalidContactKey:
            continue
        record = load_state(ws, key)
        if (
            record.state is not ContactState.PAUSED
            or not record.human_takeover
            or not record.since
            or now - parse_iso(record.since) < timedelta(minutes=minutes)
        ):
            continue
        resume_contact(ws, key, by="auto", now=now)
        actions += _notices(
            t(
                "auto_resumed",
                lang,
                name=_display_name(ws, key),
                number=str(key),
                minutes=str(minutes),
            )
        )
    return actions


def due_reminders(ws: Workspace, now: datetime) -> list[Action]:
    policy = Policy.load(ws)
    lang = _lang(ws)
    actions: list[Action] = list(due_auto_resumes(ws, now))
    for row in open_escalations(ws):
        if row.get("last_reminder_at"):
            continue
        waited = now - parse_iso(row["opened_at"])
        if waited >= timedelta(hours=policy.handoff_reminder_hours):
            key = ContactKey.parse(row["contact"])
            row["last_reminder_at"] = utc_iso(now)
            write_json_atomic(_escalation_path(ws, key), row)
            actions += _notices(
                t(
                    "escalation_reminder",
                    lang,
                    name=_display_name(ws, key),
                    number=str(key),
                    hours=str(policy.handoff_reminder_hours),
                )
            )
    return actions


def report_failure(ws: Workspace, cause: str, cycle: str, now: datetime) -> list[Action]:
    """One notice per cause and billing cycle: retrying the same refusal helps nobody."""
    data = read_json(ws.path("failures.json"), {})
    data = data if isinstance(data, dict) else {}
    if data.get(cause) == cycle:
        return []
    data[cause] = cycle
    write_json_atomic(ws.path("failures.json"), data)
    return _notices(t("send_failure_notice", _lang(ws), cause=cause))


# --- applying a contact decision --------------------------------------------------------


@dataclass
class _Context:
    ws: Workspace
    key: ContactKey
    item: WorkItem
    now: datetime
    mode: Mode
    guard: Guard | None
    out: list[Action] = field(default_factory=list)
    blocked: bool = False

    quote: str | None = None

    def _event_id(self) -> str:
        return self.item.event_ids[-1]

    def _quote_target(self) -> str | None:
        """An EARLIER message of this customer's own conversation, or nothing.

        The newest message is answered with a normal message, so it is never the quote. An id the
        model made up or took from somewhere else is never used.
        """
        if not self.quote:
            return None
        newest = self._event_id()
        lines = read_lines(self.ws, self.key)
        if any(ln.message_id == self.quote and ln.event_id == newest for ln in lines):
            return None
        return self.quote if any(ln.message_id == self.quote for ln in lines) else None

    def _emit(self, text: str, *, blocked: bool = False) -> None:
        event_id = self._event_id()
        quote_id = self._quote_target()
        action: Action
        if self.mode == "assist":
            action = _add_draft(
                self.ws, str(self.key), text, event_id, quote_id, self.now, blocked=blocked
            )
        else:
            action = Reply(str(self.key), quote_id, text, event_id)
            # what we say is part of the conversation: the next turn must see it
            append_line(
                self.ws,
                self.key,
                role="assistant",
                text=text,
                event_id=f"reply-{event_id}",
                at=utc_iso(self.now),
            )
        self.out.append(self.guard(action) if self.guard else action)

    def say(self, text: str) -> None:
        """Queue a message to the customer, after the egress guard. Nothing skips this check."""
        verdict = check_egress(self.ws, text, contact=self.key)
        if verdict.ok:
            self._emit(text)
            return
        self.blocked = True
        lang = _lang(self.ws)
        why = ", ".join(t(f"egress_{r}", lang) for r in verdict.reasons)
        _count(self.ws, "egress_blocked")
        if load_state(self.ws, self.key).state is ContactState.DO_NOT_CONTACT:
            # the customer asked for silence: keep it, only tell the people
            self.out += _notices(t("reply_blocked_notice", lang, number=str(self.key), why=why))
            return
        if self.mode == "assist":
            self._emit(t("blocked_draft", lang, why=why), blocked=True)
        else:
            self._emit(t("handoff_default", lang))
        reason = t("reason_reply_blocked", lang, why=why)
        self.out += _open_escalation(
            self.ws,
            self.key,
            Escalation(reason, "", "normal"),
            self.now,
            load_state(self.ws, self.key),
        )


def _count(ws: Workspace, name: str) -> None:
    stats = read_json(ws.path("stats.json"), {})
    stats = stats if isinstance(stats, dict) else {}
    stats[name] = int(stats.get(name, 0)) + 1
    write_json_atomic(ws.path("stats.json"), stats)


def _count_reply(ws: Workspace, key: ContactKey, now: datetime) -> None:
    record = load_state(ws, key)
    stamps = list(record.counters.get("replies", []))[-99:] + [utc_iso(now)]
    save_state(ws, key, replace(record, counters={**record.counters, "replies": stamps}))


def _remember(ws: Workspace, key: ContactKey, decision: Decision, now: datetime) -> None:
    if not decision.facts:
        return
    card = Card.load(ws, key)
    card.merge([Fact(f.key, f.value, "inferred", utc_iso(now)) for f in decision.facts])
    card.save()


def apply_contact_decision(
    ws: Workspace,
    item: WorkItem,
    decision: Decision,
    now: datetime,
    *,
    mode: Mode,
    guard: Guard | None = None,
) -> list[Action]:
    assert item.contact is not None
    key = ContactKey.parse(item.contact)
    record = load_state(ws, key)
    if record.state is not ContactState.ACTIVE or load_config(ws).get("paused"):
        return []
    ctx = _Context(ws, key, item, now, mode, guard, quote=decision.quote)
    policy = Policy.load(ws)
    lang = _lang(ws)

    if _looks_like_loop(ws, key):
        return _pause(ws, key, "repeated_messages", now, record)

    stamps = list(record.counters.get("replies", []))
    if (
        _recent(stamps, now, timedelta(hours=1)) >= policy.reply_cap_per_hour
        or _recent(stamps, now, timedelta(days=1)) >= policy.reply_cap_per_day
    ):
        return _pause(ws, key, "rate_cap", now, record)

    if decision.language:
        record = replace(load_state(ws, key), language=decision.language)
        save_state(ws, key, record)

    escalation = decision.escalate
    if escalation is None and decision.reply is None and decision.state is None:
        escalation = Escalation(t("reason_empty_decision", lang), "", "normal")

    if decision.state == "do_not_contact":
        save_state(
            ws,
            key,
            replace(load_state(ws, key), state=ContactState.DO_NOT_CONTACT, since=utc_iso(now)),
        )
        drop_drafts_for(ws, str(key))  # nothing prepared before the opt-out may go out afterwards
        drop_outbox_for(ws, str(key))  # nor a reply that was queued but not yet delivered
        ctx.say(decision.reply or t("dnc_default", lang))
        if not ctx.blocked:
            _remember(ws, key, decision, now)
        return ctx.out

    if escalation is not None:
        ctx.say(decision.reply or t("handoff_default", lang))
        if not ctx.blocked:
            _remember(ws, key, decision, now)
            ctx.out += _open_escalation(ws, key, escalation, now, load_state(ws, key))
        return ctx.out

    if decision.reply:
        ctx.say(decision.reply)
    if not ctx.blocked:
        _remember(ws, key, decision, now)
        if decision.reply and mode == "auto":
            _count_reply(ws, key, now)
    return ctx.out
