"""The unattended runner: poll without a model, wake a fresh process only when there is work.

It holds the API key in memory, uses it for the REST calls itself (events, message text, replies,
notices) and gives it to a child process only when that child is an administrator or attendant one.
A customer's turn runs in a process with no tools; the runner sends what comes out, after the checks
in :mod:`tn_assist.apply`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from tn_assist import __version__
from tn_assist.apply import (
    Action,
    InvalidDecision,
    NotifyAdmins,
    NotifySelfChat,
    due_reminders,
    report_failure,
)
from tn_assist.config import load_config
from tn_assist.deliver import expand, outbox_add, outbox_done, outbox_pending
from tn_assist.events import Event, parse_events
from tn_assist.i18n import Lang, t
from tn_assist.ingest import collect_since, ingest
from tn_assist.memory import purge_expired, read_lines_at, set_text_at
from tn_assist.pack import build_pack
from tn_assist.paths import ContactKey, Workspace
from tn_assist.profiles import Child, Role, UnsupportedHarness, build_child
from tn_assist.queue import (
    WorkItem,
    complete,
    lease_next,
    mark_hydrated,
    release_for_retry,
)
from tn_assist.state import ContactState, load_state
from tn_assist.store import WorkspaceLock, append_jsonl, read_json, read_jsonl, write_json_atomic
from tn_assist.threads import thread_path
from tn_assist.timeutil import parse_iso, utc_iso
from tn_assist.version import check as version_check
from tn_assist.version import fetch_latest
from tn_assist.workflow import apply_decision_text, run_console_item

EVENT_TYPES = ("message.received", "message.replied", "message.sent")
KINDS = ("contact", "admin", "attendant", "compaction")
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 60
LEASE_SECONDS = 900
QUOTA_CODES = frozenset(
    {"payment_required", "rate_limit_exceeded", "number_not_connected", "feature_not_available"}
)


class ApiError(Exception):
    def __init__(self, status: int, code: str = "") -> None:
        super().__init__(f"{status} {code}".strip())
        self.status, self.code = status, code


class Api(Protocol):
    def events(self, params: dict[str, str]) -> list[Event]: ...
    def message_text(self, number_ref: str, message_id: str) -> str | None: ...
    def reply(self, number_ref: str, message_id: str, text: str, key: str) -> None: ...
    def send(self, number_ref: str, to: str, text: str, key: str) -> None: ...


class RestApi:
    """The Turbo Notify REST API. The only place the runner uses the key."""

    def __init__(self, base: str, key: str, number_id: str, *, timeout: float = 20.0) -> None:
        self._base = base.rstrip("/")
        self._key = key
        self._number_id = number_id
        self._timeout = timeout

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None, key: str | None = None
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if key:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(
            self._base + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                raw = response.read()
        except urllib.error.HTTPError as error:
            code = ""
            try:
                payload = json.loads(error.read() or b"{}")
                code = str(payload.get("error", "")) if isinstance(payload, dict) else ""
            except ValueError:
                pass
            raise ApiError(error.code, code) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ApiError(0, "network") from error
        return json.loads(raw) if raw else {}

    def events(self, params: dict[str, str]) -> list[Event]:
        query = [("types[]", kind) for kind in EVENT_TYPES] + [("number_id", self._number_id)]
        query += list(params.items())
        payload = self._call("GET", "/v1/events?" + urllib.parse.urlencode(query))
        return parse_events(payload)

    def message_text(self, number_ref: str, message_id: str) -> str | None:
        try:
            data = self._call("GET", f"/v1/numbers/{number_ref}/messages/{message_id}")
        except ApiError as error:
            if error.status == 404:
                return None
            raise
        if not isinstance(data, dict) or data.get("content_expired"):
            return None
        for field_name in ("text",):
            if isinstance(data.get(field_name), str) and data[field_name].strip():
                return str(data[field_name])
        for media in ("image", "video", "document"):
            block = data.get(media)
            if isinstance(block, dict) and isinstance(block.get("caption"), str):
                return str(block["caption"])
        audio = data.get("audio")
        if isinstance(audio, dict):
            transcription = audio.get("transcription")
            if isinstance(transcription, dict) and isinstance(transcription.get("text"), str):
                return str(transcription["text"])
        return ""

    def reply(self, number_ref: str, message_id: str, text: str, key: str) -> None:
        self._call(
            "POST",
            f"/v1/numbers/{number_ref}/messages/{message_id}/reply",
            {"content": {"text": text}},
            key,
        )

    def send(self, number_ref: str, to: str, text: str, key: str) -> None:
        self._call(
            "POST",
            f"/v1/numbers/{number_ref}/messages",
            {"to": {"number": "+" + to}, "content": {"text": text}},
            key,
        )


class ChildFailed(Exception):
    pass


Spawn = Callable[[Child], str]


def run_child(child: Child, timeout: float = 600.0) -> str:
    try:
        done = subprocess.run(  # noqa: S603
            child.argv,
            input=child.stdin,
            env=child.env,
            cwd=str(child.cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ChildFailed(type(error).__name__) from error
    if done.returncode != 0:
        raise ChildFailed(f"exit {done.returncode}")
    if child.output_file is not None and child.output_file.exists():
        return child.output_file.read_text(encoding="utf-8")
    return done.stdout


@dataclass
class RunnerConfig:
    workspace: Workspace
    harness: str
    api_key: str = field(repr=False, default="")
    api_base: str = "https://api.turbonotify.com"
    mcp_url: str = "https://mcp.turbonotify.com"
    interval_s: int = 20
    model: str | None = None
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))


@dataclass
class CycleReport:
    busy: bool = False
    primed: bool = False
    ingested: int = 0
    processed: int = 0
    failed: int = 0
    sent: int = 0
    children: int = 0


def _log(ws: Workspace, event: str, **fields: Any) -> None:
    """Event names, ids and counts only: never message text, never a number, never a credential."""
    safe = {k: v for k, v in fields.items() if isinstance(v, (int, str, bool)) and k != "text"}
    append_jsonl(
        ws.path("logs", "runner.jsonl"),
        {"at": utc_iso(datetime.now(timezone.utc)), "event": event, **safe},
    )


def check_harness(harness: str) -> None:
    if harness not in ("claude-code", "codex"):
        raise UnsupportedHarness(harness)


def _execute(cfg: RunnerConfig, api: Api, now: datetime, report: CycleReport) -> int:
    """Try every delivery in the outbox. Quota and plan refusals are reported once and dropped."""
    ws = cfg.workspace
    number_ref = ws.num_id
    refused = 0
    for op in outbox_pending(ws):
        try:
            if op["op"] == "reply":
                api.reply(number_ref, op["message_id"], op["text"], op["idempotency_key"])
            else:
                api.send(number_ref, op["to"], op["text"], op["idempotency_key"])
        except ApiError as error:
            if error.status == 0 or error.status >= 500:
                _log(ws, "delivery_retry", status=error.status)
                continue  # transient: the key makes the retry safe
            outbox_done(ws, op)
            refused += 1
            _log(ws, "delivery_refused", status=error.status, code=error.code)
            if error.code in QUOTA_CODES or error.status in (402, 409, 429):
                cause = error.code or str(error.status)
                notices = report_failure(ws, cause, now.strftime("%Y-%m"), now)
                outbox_add(ws, expand(ws, notices, seed=f"failure-{cause}"))
            continue
        outbox_done(ws, op)
        report.sent += 1
    return refused


def _hydrate(cfg: RunnerConfig, api: Api, item: WorkItem) -> None:
    ws = cfg.workspace
    lines = {ln.event_id: ln for ln in read_lines_at(thread_path(ws, item.kind, item.contact))}
    for event_id in item.event_ids:
        if event_id in item.hydrated_ids:
            continue
        line = lines.get(event_id)
        message_id = line.message_id if line else None
        text: str | None = None
        if message_id:
            try:
                text = api.message_text(ws.num_id, message_id)
            except ApiError:
                text = None
        set_text_at(thread_path(ws, item.kind, item.contact), event_id, text)
        mark_hydrated(ws, item.id, event_id)


def _role(item: WorkItem) -> Role:
    return item.kind  # type: ignore[return-value]


def _decide(
    cfg: RunnerConfig, item: WorkItem, spawn: Spawn, now: datetime, report: CycleReport
) -> str:
    ws = cfg.workspace
    pack = build_pack(ws, item, now=now)
    with tempfile.TemporaryDirectory(prefix="tn-assist-") as scratch:
        child = build_child(
            cfg.harness,
            _role(item),
            pack.text,
            scratch=Path(scratch),
            mcp_url=cfg.mcp_url,
            key=cfg.api_key or None,
            base_env=cfg.env,
            model=cfg.model,
        )
        report.children += 1
        return spawn(child)


def _answerable(ws: Workspace, item: WorkItem) -> bool:
    assert item.contact is not None
    state = load_state(ws, ContactKey.parse(item.contact)).state
    return state is ContactState.ACTIVE and not load_config(ws).get("paused")


def _process(
    cfg: RunnerConfig, api: Api, item: WorkItem, spawn: Spawn, now: datetime, report: CycleReport
) -> None:
    ws = cfg.workspace
    if item.kind == "contact" and not _answerable(ws, item):
        complete(ws, item.id)  # paused, handed off or opted out: no model, no reply
        return
    _hydrate(cfg, api, item)
    actions: list[Action] = []
    if item.kind in ("admin", "attendant"):
        outcome = run_console_item(ws, item, now)
        actions += outcome.actions
        if outcome.needs_model:
            raw = _decide(cfg, item, spawn, now, report)
            actions += apply_decision_text(ws, item, raw, now)
    else:
        raw = _decide(cfg, item, spawn, now, report)
        actions += apply_decision_text(ws, item, raw, now)
    if actions:
        outbox_add(ws, expand(ws, actions, seed=item.id))


def _give_up(cfg: RunnerConfig, item: WorkItem, now: datetime) -> None:
    """An item that keeps failing is handed to a person instead of looping forever."""
    ws = cfg.workspace
    if item.kind == "contact":
        try:
            actions = apply_decision_text(
                ws,
                item,
                json.dumps(
                    {
                        "escalate": {
                            "reason": "automatic answer failed",
                            "summary": "",
                            "urgency": "normal",
                        }
                    }
                ),
                now,
            )
            outbox_add(ws, expand(ws, actions, seed=item.id))
        except InvalidDecision:
            complete(ws, item.id)
        return
    complete(ws, item.id)


def run_cycle(
    cfg: RunnerConfig,
    api: Api,
    *,
    spawn: Spawn = run_child,
    now: datetime | None = None,
    fetch_version: Callable[[], Any] | None = fetch_latest,
) -> CycleReport:
    check_harness(cfg.harness)
    ws = cfg.workspace
    now = now or datetime.now(timezone.utc)
    report = CycleReport()
    lock = WorkspaceLock(ws.path("runner.lock"))
    if not lock.acquire(ttl_s=LEASE_SECONDS):
        report.busy = True
        return report
    try:
        _execute(cfg, api, now, report)
        cursor_state = read_json(ws.path("cursor.json"), {})
        cursor = cursor_state.get("last_event_id") if isinstance(cursor_state, dict) else None
        found = collect_since(api.events, cursor)
        if cursor is None:
            if found.newest_id:
                write_json_atomic(
                    ws.path("cursor.json"),
                    {"last_event_id": found.newest_id, "last_tick_at": utc_iso(now)},
                )
            report.primed = True
            return report
        result = ingest(ws, found.events, now)
        report.ingested = len(found.events)
        lock.heartbeat()
        notices: list[Action] = []
        lang: Lang = "en" if load_config(ws).get("lang") == "en" else "pt"
        if result.gap_alarm:
            gaps = read_jsonl(ws.path("gaps.jsonl"))
            last = gaps[-1] if gaps else {"from": "?", "to": utc_iso(now)}
            text = t("gap_notice", lang, start=last["from"], end=last["to"])
            notices += [NotifyAdmins(text), NotifySelfChat(text)]
        if result.first_foreign:
            notices.append(NotifyAdmins(t("foreign_notice", lang)))
        if notices:
            outbox_add(ws, expand(ws, notices, seed=f"ingest-{utc_iso(now)}"))
        while True:
            item = lease_next(ws, KINDS, ttl_s=LEASE_SECONDS, now=now)
            if item is None:
                break
            if item.attempts > MAX_ATTEMPTS:
                _give_up(cfg, item, now)
                report.failed += 1
                continue
            try:
                _process(cfg, api, item, spawn, now, report)
                report.processed += 1
            except (ChildFailed, InvalidDecision) as error:
                report.failed += 1
                _log(
                    ws,
                    "item_failed",
                    kind=item.kind,
                    error=type(error).__name__,
                    attempts=item.attempts,
                )
                release_for_retry(ws, item.id, now, RETRY_DELAY_SECONDS)
            lock.heartbeat()
        reminders = due_reminders(ws, now)
        if reminders:
            outbox_add(ws, expand(ws, reminders, seed=f"reminder-{utc_iso(now)[:13]}"))
        config = load_config(ws)
        notice = version_check(
            ws, __version__, fetch_version, now, harness=config.get("harness"), lang=lang
        )
        if notice is not None:
            announced = read_json(ws.path("update-announced.json"), {})
            if not isinstance(announced, dict) or announced.get("version") != notice.latest:
                write_json_atomic(ws.path("update-announced.json"), {"version": notice.latest})
                outbox_add(
                    ws,
                    expand(ws, [NotifyAdmins(notice.text(lang))], seed=f"update-{notice.latest}"),
                )
        purge_state = read_json(ws.path("purged.json"), {})
        last_purge = purge_state.get("at") if isinstance(purge_state, dict) else None
        if not last_purge or (now - parse_iso(last_purge)).total_seconds() > 86_400:
            purge_expired(ws, now)
            write_json_atomic(ws.path("purged.json"), {"at": utc_iso(now)})
        if _execute(cfg, api, now, report):
            _execute(cfg, api, now, report)  # a refusal queued a notice; send it in this cycle
        _log(
            ws,
            "cycle",
            ingested=report.ingested,
            processed=report.processed,
            failed=report.failed,
            children=report.children,
        )
        return report
    finally:
        lock.release()


def run_forever(cfg: RunnerConfig, api: Api, *, once: bool = False) -> None:
    while True:
        run_cycle(cfg, api)
        if once:
            return
        time.sleep(max(5, min(cfg.interval_s, 600)))
