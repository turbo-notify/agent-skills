"""Drives the helper's own runner cycle with a real model and an in-memory Turbo Notify API.

Nothing of the customer turn is re-implemented here. A cycle ingests the customer's event,
fetches the message text, builds the pack with ``pack.build_pack``, starts the child process that
``profiles.build_child`` describes for the ``contact`` role of ``claude-code``, parses the answer
with the helper's own parser, runs the egress check and queues the delivery. The only stand-in is
the Turbo Notify API: events come from a list, and "sending" appends to a list.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import itertools
import json
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from evals.attendant.cases import Shop
from tn_assist import cli
from tn_assist.apply import parse_decision, resume_contact
from tn_assist.config import load_config, save_config
from tn_assist.events import Event, parse_events
from tn_assist.i18n import Lang, t
from tn_assist.knowledge import write_knowledge
from tn_assist.paths import ContactKey, Workspace
from tn_assist.profiles import Child, build_child
from tn_assist.runner import ChildFailed, RunnerConfig, run_child, run_cycle
from tn_assist.state import ContactState, load_state
from tn_assist.store import read_json, read_jsonl
from tn_assist.timeutil import utc_iso
from tn_assist.tokens import estimate_tokens

ORG = "org_" + "e" * 32
NUM = "num_" + "f" * 32
START = datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc)
API_BASE = "https://api.example.test"
MCP_URL = "https://mcp.example.test"
HISTORY_SENDER = "5511900000001"
#: Seconds to wait before the cycle that retries a failed item (the runner backs off 1, 5, 25 min).
RETRY_AFTER = (61, 301, 1501)
_SCRATCH = re.compile(r"^.*/mcp\.json$")
LANGS: tuple[Lang, ...] = ("pt", "en")


@dataclass(frozen=True)
class Sent:
    to: str
    text: str
    key: str
    quote: Optional[str]


class FakeApi:
    """The Turbo Notify API as the runner uses it, held in memory."""

    def __init__(self) -> None:
        self.stream: list[Event] = []  # oldest first
        self.texts: dict[str, str] = {}
        self.sent: list[Sent] = []
        self._counter = itertools.count(1)

    def inbound(self, sender: str, name: str, text: str, *, at: datetime, connected: str) -> str:
        """A customer's message as the platform reports it: a 50 character preview only."""
        message_id = "msg_" + format(next(self._counter), "032x")
        stamp = utc_iso(at)

        def party(number: str, label: str) -> dict[str, Any]:
            return {"contact_id": "ct_" + number.zfill(32)[-32:], "name": label, "number": number}

        raw = {
            "id": "evt_" + format(next(self._counter), "032x"),
            "type": "message.received",
            "organization_id": ORG,
            "number_alias": "main",
            "number_id": NUM,
            "created_at": stamp,
            "data": {
                "id": message_id,
                "from": party(sender, name),
                "to": {**party(connected, "Loja"), "alias": "main"},
                "preview": text[:50],
                "content_type": "text",
                "direction": "inbound",
                "channel": "whatsapp",
                "origin": "native",
                "at": stamp,
            },
        }
        self.stream.extend(parse_events([raw]))
        self.texts[message_id] = text
        return message_id

    def events(self, params: dict[str, str]) -> list[Event]:
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

    def message_text(self, number_ref: str, message_id: str) -> Optional[str]:
        return self.texts.get(message_id)

    def send(
        self, number_ref: str, to: str, text: str, key: str, quote: Optional[str] = None
    ) -> None:
        self.sent.append(Sent(to, text, key, quote))


@dataclass
class ModelCall:
    """One start of the child process: what it was given and what it answered."""

    argv: list[str]
    prompt: str
    output: str
    elapsed_s: float
    error: Optional[str]


class Recorder:
    """The ``spawn`` the runner is given: the helper's own ``run_child``, plus a record."""

    def __init__(self, timeout: float, run: Callable[..., str] = run_child) -> None:
        self.timeout = timeout
        self.calls: list[ModelCall] = []
        self._run = run

    def __call__(self, child: Child) -> str:
        argv = [_SCRATCH.sub("<scratch>/mcp.json", part) for part in child.argv]
        started = time.monotonic()
        try:
            output = self._run(child, timeout=self.timeout)
        except ChildFailed as error:
            elapsed = time.monotonic() - started
            self.calls.append(
                ModelCall(argv, child.stdin, "", elapsed, f"{type(error).__name__}: {error}")
            )
            raise
        self.calls.append(ModelCall(argv, child.stdin, output, time.monotonic() - started, None))
        return output


def _no_model(child: Child) -> str:
    raise AssertionError("the priming cycle must not start a model")


def _no_sleep(seconds: float) -> None:
    return None


def create_workspace(shop: Shop, base: Path) -> Workspace:
    """A workspace set up the way the skill sets one up: ``init``, then the owner's knowledge."""
    argv = [
        "--base", str(base), "--org", ORG, "--number", NUM, "init",
        "--connected", shop.connected_number, "--business", shop.business, "--lang", shop.lang,
        "--harness", "claude-code", "--admin", shop.admin_number,
    ]  # fmt: skip
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    if code != 0:
        raise RuntimeError(f"init failed: {out.getvalue()}")
    ws = Workspace.open(base, ORG, NUM)
    for item in shop.files:
        write_knowledge(
            ws,
            item.path,
            item.text,
            visibility=item.visibility,
            description=item.description,
        )
    config = load_config(ws)
    config["mode"] = "auto"
    save_config(ws, config)
    return ws


@dataclass
class TurnResult:
    """What one customer message led to."""

    number: str
    text: str
    delivered: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    calls: list[ModelCall] = field(default_factory=list)
    attempts: int = 0
    raw: str = ""
    decision: Optional[dict[str, Any]] = None
    decision_error: str = ""
    errors: list[str] = field(default_factory=list)
    escalated: bool = False
    state: str = ""
    state_language: Optional[str] = None
    egress_blocked: int = 0
    pack_sources: list[str] = field(default_factory=list)
    pack_tokens: int = 0
    reopened: bool = False
    #: The model said nothing needed to be sent and the helper agreed: handled, no reply.
    silenced: bool = False


class Session:
    """One shop workspace and the customers who write to it, turn by turn."""

    def __init__(
        self,
        shop: Shop,
        base: Path,
        *,
        recorder: Recorder,
        model: Optional[str],
        env: Mapping[str, str],
        step: timedelta = timedelta(minutes=1),
    ) -> None:
        self.shop = shop
        self.ws = create_workspace(shop, base)
        self.api = FakeApi()
        self.recorder = recorder
        self.step = step
        self.now = START
        self.cfg = RunnerConfig(
            self.ws, "claude-code", api_key="", api_base=API_BASE, mcp_url=MCP_URL,
            interval_s=20, model=model, env=dict(env),
        )  # fmt: skip
        self.api.inbound(
            HISTORY_SENDER, "Cliente antigo", "mensagem antiga",
            at=self.now - timedelta(hours=1), connected=shop.connected_number,
        )  # fmt: skip
        first = run_cycle(
            self.cfg, self.api, spawn=_no_model, now=self.now, fetch_version=None, sleep=_no_sleep
        )
        if not first.primed:
            raise RuntimeError("the first cycle should only prime the cursor")

    def _cycle(self) -> None:
        run_cycle(
            self.cfg, self.api, spawn=self.recorder, now=self.now, fetch_version=None,
            sleep=_no_sleep,
        )  # fmt: skip

    def _egress_blocked(self) -> int:
        stats = read_json(self.ws.path("stats.json"), {})
        return int(stats.get("egress_blocked", 0)) if isinstance(stats, dict) else 0

    def _failures(self) -> list[str]:
        rows = read_jsonl(self.ws.path("logs", "runner.jsonl"))
        failed = (
            "item_failed",
            "item_rejected",
        )  # the process did not run / its answer was refused
        return [str(r.get("error")) for r in rows if r.get("event") in failed]

    def _silenced(self) -> int:
        rows = read_jsonl(self.ws.path("logs", "runner.jsonl"))
        return sum(1 for r in rows if r.get("event") == "contact_silenced")

    def say(self, number: str, name: str, text: str, *, retries: int = 2) -> TurnResult:
        """The customer writes; one runner cycle answers (more cycles only to retry a failure)."""
        key = ContactKey.parse(number)
        result = TurnResult(number=str(key), text=text)
        if load_state(self.ws, key).state is not ContactState.ACTIVE:
            # a person would have handed the conversation back; the next turn must be answered
            resume_contact(self.ws, key, by="evaluation", now=self.now)
            result.reopened = True
        self.now += self.step
        self.api.inbound(number, name, text, at=self.now, connected=self.shop.connected_number)
        calls_before = len(self.recorder.calls)
        sent_before = len(self.api.sent)
        blocked_before = self._egress_blocked()
        failures_before = len(self._failures())
        silenced_before = self._silenced()
        self._cycle()
        result.attempts = 1
        while (
            not self._answered(str(key), sent_before)
            and self._silenced() == silenced_before
            and result.attempts <= retries
        ):
            self.now += timedelta(seconds=RETRY_AFTER[result.attempts - 1])
            self._cycle()
            result.attempts += 1
        result.silenced = self._silenced() > silenced_before
        fresh = self.api.sent[sent_before:]
        result.delivered = [s.text for s in fresh if s.to == str(key)]
        result.notices = [s.text for s in fresh if s.to != str(key)]
        result.calls = self.recorder.calls[calls_before:]
        result.errors = self._failures()[failures_before:]
        result.egress_blocked = self._egress_blocked() - blocked_before
        record = load_state(self.ws, key)
        result.state = record.state.value
        result.state_language = record.language
        result.escalated = record.state is ContactState.HANDOFF
        answered = [c for c in result.calls if c.error is None]
        if answered:
            last = answered[-1]
            result.raw = last.output
            result.pack_sources = re.findall(r"(?m)^### (\S+)$", last.prompt)
            result.pack_tokens = estimate_tokens(last.prompt)
            try:
                result.decision = dataclasses.asdict(parse_decision("contact", last.output))
            except Exception as error:  # the helper's own parser decides what is valid
                result.decision_error = f"{type(error).__name__}: {error}"
        return result

    def _answered(self, number: str, sent_before: int) -> bool:
        return any(s.to == number for s in self.api.sent[sent_before:])


# --- the model --------------------------------------------------------------------------


def cli_version(binary: str = "claude") -> str:
    try:
        done = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip()


def detect_model(env: Mapping[str, str], model: Optional[str], timeout: float = 120.0) -> list[str]:
    """The model id(s) the contact child resolves to, read from the CLI's own usage report.

    This is a separate one-word call, made with the contact child's exact flags, used only to name
    the model in the results. The cases themselves never run through it.
    """
    with tempfile.TemporaryDirectory(prefix="tn-eval-model-") as scratch:
        child = build_child(
            "claude-code", "contact", "Answer with the single word: ok",
            scratch=Path(scratch), mcp_url=MCP_URL, key=None, base_env=env, model=model,
        )  # fmt: skip
        done = subprocess.run(
            [*child.argv, "--output-format", "json"],
            input=child.stdin, env=child.env, cwd=str(child.cwd),
            capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip
    try:
        usage = json.loads(done.stdout).get("modelUsage", {})
    except ValueError:
        return []
    return sorted(str(name) for name in usage)


def disclosure_language(text: str) -> Optional[Lang]:
    """The language of the AI line trusted code puts before a chat's first answer, if present."""
    for lang in LANGS:
        if text.startswith(t("ai_disclosure", lang)):
            return lang
    return None


def strip_disclosure(text: str) -> str:
    lang = disclosure_language(text)
    if lang is None:
        return text
    return text[len(t("ai_disclosure", lang)) :].lstrip()


def temp_base() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="tn-eval-")
