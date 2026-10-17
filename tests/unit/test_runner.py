from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.support import factories as f
from tests.support.fakes import FakeApi, FakeHarness
from tests.support.workspace import make_workspace
from tn_assist.apply import list_drafts
from tn_assist.config import load_config, save_config
from tn_assist.deliver import outbox_add
from tn_assist.paths import ContactKey, Workspace
from tn_assist.profiles import KEY_ENV, UnsupportedHarness
from tn_assist.queue import WorkItem, enqueue, lease_next
from tn_assist.runner import (
    ApiError,
    RestApi,
    RunnerConfig,
    run_cycle,
)
from tn_assist.state import ContactState, load_state
from tn_assist.store import WorkspaceLock, read_jsonl

KEY = "tn_live_" + "k" * 32
T0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ANA = ContactKey.parse(f.ANA)
OLD = f.inbound_text(preview="mensagem antiga")


def setup(tmp_path: Path, *, mode: str = "auto") -> tuple[Workspace, RunnerConfig, FakeApi]:
    ws = make_workspace(tmp_path)
    config = load_config(ws)
    config.update({"mode": mode, "business_name": "Loja Aurora", "harness": "claude-code"})
    save_config(ws, config)
    api = FakeApi()
    api.add(OLD)
    cfg = RunnerConfig(ws, "claude-code", api_key=KEY, env={"HOME": "/home/x", "PATH": "/usr/bin"})
    run_cycle(
        cfg, api, spawn=FakeHarness(), now=T0, fetch_version=None
    )  # first cycle primes the cursor
    return ws, cfg, api


def cycle(cfg: RunnerConfig, api: FakeApi, harness: FakeHarness, at: datetime = T0, **kw: Any):  # type: ignore[no-untyped-def]
    return run_cycle(
        cfg, api, spawn=harness, now=at, fetch_version=kw.pop("fetch_version", None), **kw
    )


def test_the_first_cycle_primes_the_cursor_and_does_not_answer_history(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    api = FakeApi()
    api.add(OLD)
    harness = FakeHarness()
    report = run_cycle(
        RunnerConfig(ws, "claude-code", api_key=KEY), api, spawn=harness, now=T0, fetch_version=None
    )
    assert report.primed and harness.children == [] and api.replies == [] and api.sends == []


def test_an_idle_cycle_starts_no_model(tmp_path: Path) -> None:  # the cost claim
    ws, cfg, api = setup(tmp_path)
    harness = FakeHarness()
    for minute in range(1, 4):
        report = cycle(cfg, api, harness, T0 + timedelta(minutes=minute))
        assert report.children == 0 and report.processed == 0
    assert harness.children == []


def test_a_new_message_starts_one_fresh_process_per_customer_and_replies_after_the_check(
    tmp_path: Path,
) -> None:
    ws, cfg, api = setup(tmp_path)
    ana = f.inbound_text(preview="quanto custa?", created_at="2026-10-17T12:01:00Z")
    bruno = f.inbound_text(
        sender=f.BRUNO,
        sender_name="Bruno Lima",
        preview="tem entrega?",
        created_at="2026-10-17T12:01:05Z",
    )
    api.add(ana, bruno)
    api.texts[ana["data"]["id"]] = "Oi, quanto custa o plano anual?"
    api.texts[bruno["data"]["id"]] = "Oi, tem entrega para Salvador?"
    harness = FakeHarness(
        rules=[
            ("plano anual", '{"reply": "Custa R$ 490."}'),
            ("Salvador", '{"reply": "Sim, entregamos."}'),
        ]
    )
    report = cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert report.children == 2 and report.processed == 2
    assert len(harness.children) == 2
    for child in harness.children:
        message = child.stdin.split("## New message")[1]
        assert ("plano anual" in message) != ("Salvador" in message)  # one customer per process
    assert {r[2] for r in api.replies} == {"Custa R$ 490.", "Sim, entregamos."}
    assert {r[3] for r in api.replies} == {ana["id"], bruno["id"]}  # idempotency key = event id


def test_the_message_text_is_fetched_before_the_model_turn(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    e = f.inbound_text(preview="Olá, quero saber o pre...", created_at="2026-10-17T12:01:00Z")
    api.add(e)
    api.texts[e["data"]["id"]] = "Olá, quero saber o preço do plano anual"
    harness = FakeHarness('{"reply": "ok"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert "preço do plano anual" in harness.children[0].stdin


def test_a_customer_process_has_no_tools_and_no_key_while_an_admin_one_has_read_tools(
    tmp_path: Path,
) -> None:
    ws, cfg, api = setup(tmp_path)
    c = f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z")
    a = f.inbound_text(
        sender=f.OWNER,
        preview="quantos clientes tenho essa semana?",
        created_at="2026-10-17T12:01:05Z",
    )
    api.add(c, a)
    api.texts[a["data"]["id"]] = "quantos clientes tenho essa semana?"
    harness = FakeHarness(
        rules=[
            ("clientes tenho", '{"reply": "Dois.", "command": "/resumo"}'),
            ("oi", '{"reply": "Olá!"}'),
        ]
    )
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert len(harness.children) == 2
    contact_child = next(
        c for c in harness.children if "\nCONTACT" in c.stdin or "customer_message" in c.stdin
    )
    admin_child = next(c for c in harness.children if c is not contact_child)
    assert KEY_ENV not in contact_child.env and "--allowedTools" not in contact_child.argv
    assert admin_child.env[KEY_ENV] == KEY and "--allowedTools" in admin_child.argv
    assert any(o[1] == f.OWNER and "Dois." in o[2] for o in api.sends)
    assert any(
        o[1] == f.OWNER and "Clientes" in o[2] for o in api.sends
    )  # /resumo is read only: it ran


def test_a_leak_is_blocked_before_the_runner_sends_anything(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    b = f.inbound_text(
        sender=f.BRUNO, sender_name="Bruno Lima", preview="oi", created_at="2026-10-17T12:01:00Z"
    )
    a = f.inbound_text(preview="qual o contato do Bruno?", created_at="2026-10-17T12:01:05Z")
    api.add(b, a)
    harness = FakeHarness(
        rules=[("contato do Bruno", f'{{"reply": "Ligue {f.BRUNO}"}}'), ("oi", '{"reply": "oi"}')]
    )
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    everything = json.dumps([r[2] for r in api.replies]) + json.dumps([s[2] for s in api.sends])
    assert f.BRUNO not in everything
    assert load_state(ws, ANA).state is ContactState.HANDOFF
    assert any("pessoa" in r[2] for r in api.replies)


def test_assisted_mode_sends_drafts_to_the_people_not_to_the_customer(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path, mode="assist")
    e = f.inbound_text(preview="quanto custa?", created_at="2026-10-17T12:01:00Z")
    api.add(e)
    harness = FakeHarness('{"reply": "Custa R$ 490."}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert api.replies == []
    assert {s[1] for s in api.sends} == {f.OWNER, f.CONNECTED}
    assert "/enviar 1" in api.sends[0][2] and len(list_drafts(ws)) == 1


def test_a_paused_customer_costs_no_model_turn(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z"))
    api.add(f.native_outbound_to_contact(preview="Já te atendo", created_at="2026-10-17T12:01:30Z"))
    harness = FakeHarness()
    cycle(cfg, api, harness, T0 + timedelta(minutes=2))
    assert harness.children == [] and api.replies == []
    assert load_state(ws, ANA).state is ContactState.PAUSED


def test_the_key_is_never_written_to_the_workspace_or_the_logs(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(sender=f.OWNER, preview="oi", created_at="2026-10-17T12:01:00Z"))
    harness = FakeHarness('{"reply": "ok"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    for path in ws.root.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path
    assert not any(KEY in " ".join(c.argv) for c in harness.children)
    assert (ws.root / "logs" / "runner.jsonl").exists()


def test_a_second_runner_on_the_same_workspace_does_nothing(tmp_path: Path) -> None:  # RF 5
    ws, cfg, api = setup(tmp_path)
    other = WorkspaceLock(ws.path("runner.lock"))
    assert other.acquire(ttl_s=900)
    api.add(f.inbound_text(created_at="2026-10-17T12:01:00Z"))
    report = cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=1))
    assert report.busy and api.replies == []
    other.release()


def test_an_item_left_leased_by_a_killed_runner_comes_back(tmp_path: Path) -> None:  # RF 5
    ws, cfg, api = setup(tmp_path)
    e = f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z")
    api.add(e)
    from tn_assist.events import parse_events
    from tn_assist.ingest import ingest

    ingest(ws, parse_events(f.payload(e)), T0)
    leased = lease_next(ws, ("contact",), ttl_s=900, now=T0)
    assert leased is not None  # the "killed" runner took it and never finished
    harness = FakeHarness('{"reply": "Olá!"}')
    report = cycle(cfg, api, harness, T0 + timedelta(minutes=20))
    assert report.processed == 1 and [r[2] for r in api.replies] == ["Olá!"]


def test_other_harnesses_are_refused_up_front(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    for harness in ("antigravity", "claude-desktop", "gemini"):
        with pytest.raises(UnsupportedHarness):
            run_cycle(RunnerConfig(ws, harness), FakeApi(), spawn=FakeHarness(), now=T0)


def test_a_failing_model_is_retried_and_then_the_customer_goes_to_a_person(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z"))
    harness = FakeHarness()
    harness.fail = 99
    states = []
    for minute in range(1, 7):
        cycle(cfg, api, harness, T0 + timedelta(minutes=minute))
        states.append(load_state(ws, ANA).state)
    assert states[:3] == [ContactState.ACTIVE] * 3  # three attempts, one per cycle
    assert states[3] is ContactState.HANDOFF  # then a person, with no further model call
    assert len(harness.children) == 3
    assert any("automatic answer failed" in s[2] for s in api.sends)


def test_a_garbled_answer_is_retried_not_sent(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z"))
    harness = FakeHarness("I would be happy to help!", '{"reply": "Olá!"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert api.replies == []
    cycle(cfg, api, harness, T0 + timedelta(minutes=2))
    assert [r[2] for r in api.replies] == ["Olá!"]


def test_a_transient_delivery_failure_is_retried_with_the_same_key(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    e = f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z")
    api.add(e)
    api.errors = [ApiError(503, "unavailable")]
    harness = FakeHarness('{"reply": "Olá!"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert api.replies == []
    cycle(cfg, api, harness, T0 + timedelta(minutes=2))
    assert api.replies == []  # held back for a while, not hammered
    cycle(cfg, api, harness, T0 + timedelta(minutes=8))
    assert [(r[2], r[3]) for r in api.replies] == [("Olá!", e["id"])]


def test_a_quota_refusal_is_dropped_and_reported_once(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z"))
    api.add(
        f.inbound_text(
            sender=f.BRUNO, sender_name="B", preview="oi", created_at="2026-10-17T12:01:10Z"
        )
    )
    api.errors = [ApiError(402, "payment_required"), ApiError(402, "payment_required")]
    harness = FakeHarness('{"reply": "a"}', '{"reply": "b"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert api.replies == []
    notices = [s for s in api.sends if "payment_required" in s[2]]
    assert {n[1] for n in notices} == {f.OWNER, f.CONNECTED}  # once, not once per refused reply
    again = cycle(cfg, api, harness, T0 + timedelta(minutes=2))
    assert again.sent == 0


def test_a_gap_in_service_is_announced(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(preview="oi", created_at="2026-10-17T13:00:00Z"))
    cycle(cfg, api, FakeHarness('{"reply": "ok"}'), T0 + timedelta(hours=2))
    assert any("fora do ar" in s[2] for s in api.sends)


def test_an_update_is_announced_once(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    latest = lambda: {"version": "9.9.9"}  # noqa: E731
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=1), fetch_version=latest)
    first = [s for s in api.sends if "9.9.9" in s[2]]
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=2), fetch_version=latest)
    assert first and len([s for s in api.sends if "9.9.9" in s[2]]) == len(first)


def test_logs_have_events_and_counts_but_no_text_or_numbers(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(preview="meu segredo", created_at="2026-10-17T12:01:00Z"))
    api.texts[api.stream[-1].data["id"]] = "meu segredo é 123"
    cycle(cfg, api, FakeHarness('{"reply": "ok"}'), T0 + timedelta(minutes=1))
    text = (ws.root / "logs" / "runner.jsonl").read_text()
    assert "segredo" not in text and f.ANA not in text and f.OWNER not in text
    assert all("event" in row for row in read_jsonl(ws.root / "logs" / "runner.jsonl"))


class _Handler(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def _answer(self, status: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.seen.append(
            {
                "method": self.command,
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "key": self.headers.get("Idempotency-Key"),
                "body": body.decode(),
            }
        )
        return body

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        if "/messages/msg_gone" in self.path:
            self._answer(404, {"error": "not_found"})
        elif "/messages/" in self.path:
            self._answer(200, {"content_expired": False, "text": "texto completo"})
        else:
            self._answer(200, {"object": "list", "data": [f.inbound_text()], "has_more": False})

    def do_POST(self) -> None:  # noqa: N802
        self._record()
        if self.headers.get("Idempotency-Key") == "evt_2":
            self._answer(402, {"error": "payment_required"})
        else:
            self._answer(202, {"message_id": "msg_1"})

    def log_message(self, *args: Any) -> None:
        pass


def test_the_rest_client_speaks_the_documented_api() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        api = RestApi(f"http://127.0.0.1:{server.server_port}", KEY, f.NUM)
        events = api.events({"limit": "100", "ending_before": "evt_x"})
        assert len(events) == 1
        assert api.message_text(f.NUM, "msg_1") == "texto completo"
        assert api.message_text(f.NUM, "msg_gone") is None
        api.send(f.NUM, f.ANA, "Olá", "evt_1")
        api.send(f.NUM, f.ANA, "como disse antes", "evt_3", quote="msg_" + "a" * 32)
        api.send(f.NUM, f.OWNER, "aviso", "n-1")
        with pytest.raises(ApiError) as caught:
            api.send(f.NUM, f.ANA, "x", "evt_2")
        assert caught.value.status == 402 and caught.value.code == "payment_required"
        seen = _Handler.seen
        assert all(s["auth"] == f"Bearer {KEY}" for s in seen)
        assert (
            "types%5B%5D=message.received" in seen[0]["path"]
            and f"number_id={f.NUM}" in seen[0]["path"]
        )
        assert "ending_before=evt_x" in seen[0]["path"]
        posts = [s for s in seen if s["method"] == "POST"]
        assert all(
            s["path"].endswith("/messages") for s in posts
        )  # no /reply route: a plain message
        plain = next(s for s in posts if s["key"] == "evt_1")
        assert json.loads(plain["body"]) == {
            "to": {"number": "+" + f.ANA},
            "content": {"text": "Olá"},
        }
        quoted = next(s for s in posts if s["key"] == "evt_3")
        assert json.loads(quoted["body"])["quoted_message_id"] == "msg_" + "a" * 32
        send = next(s for s in posts if s["key"] == "n-1")
        assert json.loads(send["body"]) == {
            "to": {"number": "+" + f.OWNER},
            "content": {"text": "aviso"},
        }
    finally:
        server.shutdown()


def test_an_unreachable_server_is_a_network_error() -> None:
    api = RestApi("http://127.0.0.1:1", KEY, f.NUM, timeout=0.5)
    with pytest.raises(ApiError) as caught:
        api.message_text(f.NUM, "msg_1")
    assert caught.value.status == 0


def test_item_lease_helper_still_works_after_runner_cycles(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    enqueue(ws, WorkItem.new("contact", f.ANA, ("evt_z",), T0))
    assert lease_next(ws, ("contact",), ttl_s=60, now=T0) is not None


def _one_reply(tmp_path: Path):  # type: ignore[no-untyped-def]
    ws, cfg, api = setup(tmp_path)
    e = f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z")
    api.add(e)
    return ws, cfg, api, e


def test_a_pacing_refusal_waits_for_retry_after_and_goes_through_in_the_same_cycle(
    tmp_path: Path,
) -> None:
    ws, cfg, api, e = _one_reply(tmp_path)
    api.errors = [ApiError(429, "send_rate_spacing", 1)]
    slept: list[float] = []
    report = cycle(
        cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1), sleep=slept.append
    )
    assert slept == [pytest.approx(1.3)] and report.sent == 1
    assert [r[2] for r in api.replies] == ["Olá!"]
    assert (
        json.loads((ws.root / "stats.json").read_text())
        if (ws.root / "stats.json").exists()
        else True
    )


def test_pacing_that_does_not_clear_is_held_not_reported_as_a_quota_problem(tmp_path: Path) -> None:
    ws, cfg, api, e = _one_reply(tmp_path)
    api.errors = [ApiError(429, "send_rate_spacing", 1)] * 4
    slept: list[float] = []
    cycle(cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1), sleep=slept.append)
    assert api.replies == [] and len(slept) == 3
    assert not any(
        "send_rate_spacing" in s[2] for s in api.sends
    )  # nobody is told it is a quota issue
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=8), sleep=slept.append)
    assert [r[2] for r in api.replies] == ["Olá!"]


def test_a_daily_limit_holds_the_delivery_for_the_hour_or_the_retry_after(tmp_path: Path) -> None:
    ws, cfg, api, e = _one_reply(tmp_path)
    api.errors = [ApiError(429, "warmup_limit_exceeded", 7200)]
    cycle(cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1))
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=90))
    assert api.replies == []
    cycle(cfg, api, FakeHarness(), T0 + timedelta(hours=3))
    assert [r[2] for r in api.replies] == ["Olá!"]


def test_a_delivery_that_never_goes_through_is_dropped_after_six_hours(tmp_path: Path) -> None:
    ws, cfg, api, e = _one_reply(tmp_path)
    api.reply_error = ApiError(429, "reply_ratio_too_low")
    cycle(cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1))
    for hours in (2, 4, 6, 8):
        cycle(cfg, api, FakeHarness(), T0 + timedelta(hours=hours))
    assert api.replies == []
    held = [p for p in (ws.root / "outbox").glob("*.json") if '"op": "send"' in p.read_text()]
    assert held == []  # past six hours the reply is dropped, not retried for ever


def test_a_quota_429_is_a_quota_problem_not_pacing(tmp_path: Path) -> None:
    ws, cfg, api, e = _one_reply(tmp_path)
    api.errors = [ApiError(429, "messages_quota_exceeded")]
    cycle(cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1))
    assert api.replies == [] and any("messages_quota_exceeded" in s[2] for s in api.sends)


def test_other_client_errors_are_dropped_without_a_notice(tmp_path: Path) -> None:
    ws, cfg, api, e = _one_reply(tmp_path)
    api.errors = [ApiError(404, "message_not_found")]
    cycle(cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1))
    assert api.replies == [] and api.sends == []
    assert not list((ws.root / "outbox").glob("*.json"))


def test_a_quote_the_api_refuses_does_not_cost_the_customer_their_answer(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    outbox_add(
        ws,
        [
            {
                "op": "send",
                "to": f.ANA,
                "text": "Para Salvador, 3 dias.",
                "idempotency_key": "k-quote",
                "contact": f.ANA,
                "quote": "msg_" + "e" * 32,
            }
        ],
        now=T0,
    )
    api.reject_quote = ApiError(422, "message_not_replyable")
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=1))
    assert [(r[1], r[2], r[3]) for r in api.replies] == [
        (f.ANA, "Para Salvador, 3 dias.", "k-quote")
    ]
    assert api.quotes == [None]  # sent again as a plain message


def test_a_delivery_left_by_an_older_version_is_discarded_not_fatal(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    outbox_add(
        ws,
        [
            {
                "op": "reply",
                "message_id": "msg_1",
                "text": "x",
                "idempotency_key": "k-old",
                "contact": f.ANA,
            }
        ],
        now=T0,
    )
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=1))
    assert api.replies == [] and list((ws.root / "outbox").glob("*.json")) == []


def test_a_compaction_item_is_processed_not_fatal(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    e1 = f.inbound_text(preview="pergunta um", created_at="2026-10-17T12:01:00Z")
    api.add(e1)
    cycle(cfg, api, FakeHarness('{"reply": "um"}'), T0 + timedelta(minutes=1))
    item = enqueue(ws, WorkItem.new("compaction", f.ANA, (e1["id"],), T0))
    harness = FakeHarness('{"summary": "O cliente perguntou o preco.", "facts": []}')
    report = cycle(cfg, api, harness, T0 + timedelta(minutes=2))
    assert report.processed >= 1 and report.failed == 0 and item is not None


def test_a_refused_key_or_a_reconnecting_number_holds_the_answer(tmp_path: Path) -> None:
    from tn_assist.runner import classify

    assert classify(ApiError(401, "not_authenticated")) == "transient"
    assert classify(ApiError(409, "number_not_connected")) == "transient"
    assert classify(ApiError(403, "insufficient_scope")) == "quota"  # a scope does not heal
    ws, cfg, api, e = _one_reply(tmp_path)
    api.errors = [ApiError(401, "not_authenticated")]
    cycle(cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1))
    assert api.replies == []
    assert len(list((ws.root / "outbox").glob("*.json"))) >= 1  # still there, not erased
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=10))
    assert [r[2] for r in api.replies] == ["Olá!"]
    assert any("not_authenticated" in s[2] for s in api.sends)  # the people were told, once


def test_a_failing_cycle_does_not_end_the_unattended_runner(tmp_path: Path) -> None:
    from unittest import mock

    from tn_assist.runner import run_forever

    ws, cfg, api = setup(tmp_path)
    calls = {"n": 0}

    def flaky(*a: Any, **k: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ApiError(0, "network")
        raise KeyboardInterrupt

    with (
        mock.patch("tn_assist.runner.run_cycle", flaky),
        mock.patch("tn_assist.runner.time.sleep"),
        pytest.raises(KeyboardInterrupt),
    ):
        run_forever(cfg, api)
    assert calls["n"] == 2  # the first failure was logged and the loop went on


def test_a_failing_text_fetch_retries_the_item_and_never_stores_a_missing_text(
    tmp_path: Path,
) -> None:
    ws, cfg, api = setup(tmp_path)
    e = f.inbound_text(preview="quanto custa o plano anual?", created_at="2026-10-17T12:01:00Z")
    api.add(e)
    api.texts[e["data"]["id"]] = "Texto completo da pergunta sobre o plano anual."
    original = api.message_text
    calls = {"n": 0}

    def flaky(number_ref: str, message_id: str):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise ApiError(502, "bad_gateway")
        return original(number_ref, message_id)

    api.message_text = flaky  # type: ignore[method-assign]
    harness = FakeHarness('{"reply": "Custa R$ 490."}')
    first = cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert first.failed == 1 and api.replies == []
    cycle(cfg, api, harness, T0 + timedelta(minutes=5))
    assert [r[2] for r in api.replies] == ["Custa R$ 490."]
    assert "Texto completo" in harness.children[-1].stdin  # the full text, not the preview
