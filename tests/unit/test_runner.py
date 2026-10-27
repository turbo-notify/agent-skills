from __future__ import annotations

import json
import threading
from dataclasses import replace
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
from tn_assist.i18n import t
from tn_assist.paths import ContactKey, Workspace
from tn_assist.profiles import KEY_ENV, UnsupportedHarness
from tn_assist.queue import WorkItem, enqueue, lease_next, pending_count
from tn_assist.runner import (
    ApiError,
    RestApi,
    RunnerConfig,
    run_cycle,
)
from tn_assist.state import ContactState, load_state, save_state
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
    assert {r[2] for r in api.replies} == {
        f.disclosed("Custa R$ 490."),
        f.disclosed("Sim, entregamos."),
    }
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
    assert report.processed == 1 and [r[2] for r in api.replies] == [f.disclosed("Olá!")]


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
    # attempts back off (1 min, 5 min, 25 min, 2 h, 2 h): a short model outage must not hand every
    # customer to a person, so the hand-off comes only after the fifth failed attempt
    for minute in (1, 2, 7, 32, 152, 272):
        cycle(cfg, api, harness, T0 + timedelta(minutes=minute))
        states.append(load_state(ws, ANA).state)
    assert states[:5] == [ContactState.ACTIVE] * 5
    assert states[5] is ContactState.HANDOFF  # then a person, with no further model call
    assert len(harness.children) == 5
    assert any("automatic answer failed" in s[2] for s in api.sends)


def test_a_garbled_answer_is_retried_not_sent(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z"))
    harness = FakeHarness("I would be happy to help!", '{"reply": "Olá!"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert api.replies == []
    cycle(cfg, api, harness, T0 + timedelta(minutes=2))
    assert [r[2] for r in api.replies] == [f.disclosed("Olá!")]


def message(preview: str, at: str, **kw: Any) -> dict[str, Any]:
    return f.inbound_text(preview=preview, created_at=at, **kw)


def test_a_refused_answer_is_asked_again_at_the_next_cycle_not_half_an_hour_later(
    tmp_path: Path,
) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("oi", "2026-10-17T12:01:00Z"))
    harness = FakeHarness("I would be happy to help!", '{"reply": "Olá!"}')
    first = cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert first.failed == 1 and api.replies == []
    # an outage waits 1, 5, 25 minutes; a refused answer is simply asked for again
    second = cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=5))
    assert second.processed == 1 and [r[2] for r in api.replies] == [f.disclosed("Olá!")]
    assert len(harness.children) == 2
    assert [r["event"] for r in read_jsonl(ws.path("logs", "runner.jsonl"))].count(
        "item_rejected"
    ) == 1


def test_two_refused_answers_in_a_row_hand_the_customer_over_in_their_language(
    tmp_path: Path,
) -> None:
    ws, cfg, api = setup(tmp_path)
    save_state(ws, ANA, replace(load_state(ws, ANA), language="en"))  # they have written in English
    api.add(message("hello?", "2026-10-17T12:01:00Z"))
    harness = FakeHarness("nope", "still nope", '{"reply": "never asked for"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert api.replies == []
    last = cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=5))
    assert last.failed == 1
    # the same text a reply of null gets, in the customer's language, with the AI line
    assert [r[2] for r in api.replies] == [f.disclosed(t("handoff_default", "en"), "en")]
    assert load_state(ws, ANA).state is ContactState.HANDOFF
    assert len(harness.children) == 2  # no third try
    told = [s[2] for s in api.sends if s[1] in (f.OWNER, f.CONNECTED)]
    assert any(
        t("reason_decision_unreadable", "pt") in text for text in told
    )  # the people know why


def refuse_twice(
    tmp_path: Path, text: str, *, known: str | None = None
) -> tuple[Workspace, FakeApi]:
    ws, cfg, api = setup(tmp_path)
    if known:
        save_state(ws, ANA, replace(load_state(ws, ANA), language=known))
    api.add(message(text, "2026-10-17T12:01:00Z"))
    refused = FakeHarness("nope", "still nope")
    cycle(cfg, api, refused, T0 + timedelta(minutes=1))
    cycle(cfg, api, refused, T0 + timedelta(minutes=1, seconds=5))
    return ws, api


def test_a_new_customer_who_writes_english_gets_the_hand_off_in_english(tmp_path: Path) -> None:
    """No answer of the model recorded their language: the helper reads it from their words."""
    ws, api = refuse_twice(tmp_path, "Hi! What time do you open on Saturday?")
    assert [r[2] for r in api.replies] == [f.disclosed(t("handoff_default", "en"), "en")]
    assert load_state(ws, ANA).language == "en"  # and what follows keeps speaking English


def test_the_last_message_decides_when_the_customer_changed_language(tmp_path: Path) -> None:
    _, api = refuse_twice(tmp_path, "Boa tarde, qual o prazo de troca?", known="en")
    assert [r[2] for r in api.replies] == [f.disclosed(t("handoff_default", "pt"), "pt")]


def test_a_message_with_no_clear_language_keeps_the_language_already_known(tmp_path: Path) -> None:
    ws, api = refuse_twice(tmp_path, "👍", known="en")
    assert [r[2] for r in api.replies] == [f.disclosed(t("handoff_default", "en"), "en")]
    assert load_state(ws, ANA).language == "en"


def test_a_message_with_no_clear_language_and_none_known_uses_the_workspace_language(
    tmp_path: Path,
) -> None:
    ws, api = refuse_twice(tmp_path, "👍")
    assert [r[2] for r in api.replies] == [f.disclosed(t("handoff_default", "pt"), "pt")]


def test_the_hand_off_after_two_refusals_is_what_a_reply_of_null_gets(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("oi", "2026-10-17T12:01:00Z"))
    cycle(cfg, api, FakeHarness('{"reply": null}'), T0 + timedelta(minutes=1))
    expected = [r[2] for r in api.replies]
    ws2, cfg2, api2 = setup(tmp_path / "other")
    api2.add(message("oi", "2026-10-17T12:01:00Z"))
    refused = FakeHarness("nope", "nope")
    cycle(cfg2, api2, refused, T0 + timedelta(minutes=1))
    cycle(cfg2, api2, refused, T0 + timedelta(minutes=1, seconds=5))
    assert [r[2] for r in api2.replies] == expected and expected


def test_a_refused_answer_does_not_hold_another_customer(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("pergunta de Ana", "2026-10-17T12:01:00Z"))
    api.add(message("pergunta de Bruno", "2026-10-17T12:01:05Z", sender=f.BRUNO, sender_name="B"))
    harness = FakeHarness(
        rules=[("pergunta de Ana", "garbled"), ("pergunta de Bruno", '{"reply": "ok"}')]
    )
    report = cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert report.processed == 1 and report.failed == 1
    assert [r[1] for r in api.replies] == [f.BRUNO]  # Bruno was answered in the same cycle


def test_the_messages_behind_a_refused_answer_are_answered_with_it(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("primeira pergunta", "2026-10-17T12:01:00Z"))
    harness = FakeHarness("garbled", '{"reply": "Respondi as duas."}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    api.add(message("segunda pergunta", "2026-10-17T12:01:20Z"))
    cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=30))
    prompt = harness.children[1].stdin
    assert "primeira pergunta" in prompt and "segunda pergunta" in prompt
    assert [r[2] for r in api.replies] == [f.disclosed("Respondi as duas.")]


def test_an_outage_is_not_counted_as_a_refusal_and_keeps_its_pause(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("oi", "2026-10-17T12:01:00Z"))
    harness = FakeHarness("garbled", '{"reply": "Olá!"}')
    harness.fail = 1  # the process does not run once: an outage, not an answer
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=5))
    assert harness.children and len(harness.children) == 1  # the pause of an outage still holds
    cycle(cfg, api, harness, T0 + timedelta(minutes=2, seconds=5))  # the outage pause is over
    assert api.replies == []  # the model answered, and the answer was refused: once, not twice
    cycle(cfg, api, harness, T0 + timedelta(minutes=2, seconds=10))
    assert [r[2] for r in api.replies] == [
        f.disclosed("Olá!")
    ]  # one refusal asked again, no hand-off
    assert load_state(ws, ANA).state is ContactState.ACTIVE


def test_two_refused_answers_to_an_administrator_are_told_not_dropped(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("o que você acha do movimento?", "2026-10-17T12:01:00Z", sender=f.OWNER))
    harness = FakeHarness("garbled", "garbled again")
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=5))
    assert [s[2] for s in api.sends if s[1] == f.OWNER] == [t("console_unreadable", "pt")]
    again = cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=10))
    assert again.children == 0  # nothing is left waiting behind it


def heard(api: FakeApi, text: str, at: str, **kw: Any) -> None:
    """A message whose whole text the platform can still return (hydration reads it)."""
    event = message(text, at, **kw)
    api.add(event)
    api.texts[event["data"]["id"]] = text


def events_logged(ws: Workspace) -> list[str]:
    return [str(r["event"]) for r in read_jsonl(ws.path("logs", "runner.jsonl"))]


SILENCE = '{"reply": null, "facts": [], "escalate": null, "no_reply_needed": true}'


def test_a_thumbs_up_the_model_says_needs_no_reply_is_handled_in_silence(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    heard(api, "👍", "2026-10-17T12:01:00Z")
    harness = FakeHarness(SILENCE)
    report = cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert report.processed == 1 and report.silenced == 1 and report.failed == 0
    assert api.replies == [] and api.sends == []  # no answer, no hand-off, no notice
    assert load_state(ws, ANA).state is ContactState.ACTIVE
    assert pending_count(ws) == 0 and not list(ws.path("queue", "leased").glob("*.json"))
    events = events_logged(ws)
    assert "contact_silenced" in events and "item_rejected" not in events
    # handled: the next cycle has nothing to do, and no model is started
    assert cycle(cfg, api, harness, T0 + timedelta(minutes=2)).children == 0


def test_a_silence_asked_for_a_real_question_goes_to_the_default_hand_off(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    heard(api, "ok, e o frete?", "2026-10-17T12:01:00Z")
    harness = FakeHarness(SILENCE)
    report = cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert report.silenced == 0 and report.failed == 0  # not silenced, and not a refusal either
    assert [r[2] for r in api.replies] == [f.disclosed(t("handoff_default", "pt"))]
    assert load_state(ws, ANA).state is ContactState.HANDOFF
    assert len(harness.children) == 1  # no second try: the answer was readable
    assert "item_rejected" not in events_logged(ws)
    told = [s[2] for s in api.sends if s[1] in (f.OWNER, f.CONNECTED)]
    assert any(t("reason_silence_refused", "pt") in text for text in told)


def test_a_reply_of_null_without_the_flag_is_a_hand_off_exactly_as_before(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("👍", "2026-10-17T12:01:00Z"))
    report = cycle(cfg, api, FakeHarness('{"reply": null}'), T0 + timedelta(minutes=1))
    assert report.silenced == 0
    assert [r[2] for r in api.replies] == [f.disclosed(t("handoff_default", "pt"))]
    assert load_state(ws, ANA).state is ContactState.HANDOFF


def test_the_customer_is_answered_after_a_silent_turn(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    heard(api, "obrigado", "2026-10-17T12:01:00Z")
    harness = FakeHarness(SILENCE, '{"reply": "Domingo fechado."}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    heard(api, "e vocês abrem domingo?", "2026-10-17T12:05:00Z")
    cycle(cfg, api, harness, T0 + timedelta(minutes=5))
    assert [r[2] for r in api.replies] == [f.disclosed("Domingo fechado.")]


def test_the_pack_asks_for_silence_only_in_words_and_says_what_a_null_reply_does(
    tmp_path: Path,
) -> None:
    ws, cfg, api = setup(tmp_path)
    heard(api, "obrigado", "2026-10-17T12:01:00Z")
    harness = FakeHarness(SILENCE)
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    prompt = harness.children[0].stdin
    assert '"no_reply_needed": true or false' in prompt
    assert 'A "reply" of null without it hands the customer to a person' in prompt


def test_the_second_try_names_the_rule_the_first_answer_broke(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("oi, quanto custa?", "2026-10-17T12:01:00Z"))
    harness = FakeHarness("I would be happy to help!", '{"reply": "Custa R$ 49."}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=5))
    first, second = (child.stdin for child in harness.children)
    assert "was refused" not in first
    line = second.rstrip().splitlines()[-1]
    assert line == (
        "Your previous answer to this message was refused because it had no JSON object in it. "
        "Answer again with one JSON object in the format above."
    )
    assert [r[2] for r in api.replies] == [f.disclosed("Custa R$ 49.")]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ("{not json}", "it was not valid JSON"),
        ('["a"]', "it had no JSON object in it"),
        ('{"reply": "oi", "send_to": "x"}', "it had a field that is not in the format"),
        ('{"reply": 3}', '"reply" must be text of at most 4000 characters, or null'),
        ('{"facts": "x"}', '"facts" must be a list'),
        ('{"escalate": {"reason": "r"}}', '"escalate" must be null or an object with exactly'),
        ('{"state": "stop"}', '"state" must be null or "do_not_contact"'),
        ('{"no_reply_needed": "yes"}', '"no_reply_needed" must be true, false or null'),
    ],
)
def test_each_refusal_is_named_in_one_short_line_with_none_of_the_customers_words(
    tmp_path: Path, answer: str, reason: str
) -> None:
    ws, cfg, api = setup(tmp_path)
    secret = "meu CPF é 123.456.789-00"
    api.add(message(secret, "2026-10-17T12:01:00Z"))
    harness = FakeHarness(answer, '{"reply": "ok"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=5))
    second = harness.children[1].stdin
    line = second.rstrip().splitlines()[-1]
    assert line.startswith("Your previous answer to this message was refused because ")
    assert reason in line and len(line) < 400
    assert secret not in line and "123.456" not in line


def test_the_note_does_not_outlive_the_refusal(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("primeira", "2026-10-17T12:01:00Z"))
    harness = FakeHarness("garbled", '{"reply": "ok"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=5))
    api.add(message("segunda", "2026-10-17T12:10:00Z"))
    harness.answers.append('{"reply": "de novo"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=10))
    assert "was refused" in harness.children[1].stdin
    assert "was refused" not in harness.children[2].stdin  # a new message, a clean pack


def test_an_administrators_second_try_is_told_the_rule_too(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(message("o que você acha do movimento?", "2026-10-17T12:01:00Z", sender=f.OWNER))
    harness = FakeHarness("garbled", '{"reply": "Calmo."}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    cycle(cfg, api, harness, T0 + timedelta(minutes=1, seconds=5))
    assert "was refused because it had no JSON object" in harness.children[1].stdin


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
    assert [(r[2], r[3]) for r in api.replies] == [(f.disclosed("Olá!"), e["id"])]


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
        server.server_close()
        thread.join(timeout=5)


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
    assert [r[2] for r in api.replies] == [f.disclosed("Olá!")]
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
    assert [r[2] for r in api.replies] == [f.disclosed("Olá!")]


def test_a_daily_limit_holds_the_delivery_for_the_hour_or_the_retry_after(tmp_path: Path) -> None:
    ws, cfg, api, e = _one_reply(tmp_path)
    api.errors = [ApiError(429, "warmup_limit_exceeded", 7200)]
    cycle(cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1))
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=90))
    assert api.replies == []
    cycle(cfg, api, FakeHarness(), T0 + timedelta(hours=3))
    assert [r[2] for r in api.replies] == [f.disclosed("Olá!")]


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
    assert [r[2] for r in api.replies] == [f.disclosed("Olá!")]
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
    assert [r[2] for r in api.replies] == [f.disclosed("Custa R$ 490.")]
    assert "Texto completo" in harness.children[-1].stdin  # the full text, not the preview


def test_the_first_message_on_an_account_with_no_events_is_not_skipped(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    config = load_config(ws)
    config.update({"mode": "auto", "business_name": "Loja Aurora", "harness": "claude-code"})
    save_config(ws, config)
    api = FakeApi()
    cfg = RunnerConfig(ws, "claude-code", api_key=KEY, env={"HOME": "/home/x", "PATH": "/usr/bin"})
    run_cycle(cfg, api, spawn=FakeHarness(), now=T0, fetch_version=None)  # nothing to prime on
    api.add(f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z"))
    harness = FakeHarness('{"reply": "Olá!"}')
    cycle(cfg, api, harness, T0 + timedelta(minutes=1))
    assert [r[2] for r in api.replies] == [f.disclosed("Olá!")]


def test_console_answers_survive_a_failing_model_step(tmp_path: Path) -> None:
    ws, cfg, api = setup(tmp_path)
    api.add(f.inbound_text(sender=f.OWNER, preview="/status", created_at="2026-10-17T12:01:00Z"))
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=1))
    assert any(s[1] == f.OWNER for s in api.sends)  # the command's answer went out


def test_an_unknown_429_is_waited_out_not_erased() -> None:
    from tn_assist.runner import classify

    assert classify(ApiError(429, "some_new_limit")) == "pacing"


def test_the_runner_hands_a_forgotten_takeover_back_and_tells_the_people(tmp_path: Path) -> None:
    from tn_assist.state import mark_takeover

    ws, cfg, api = setup(tmp_path)
    ws.path("memory-policy.json").write_text('{"auto_resume_after_minutes": 30}')
    api.add(f.inbound_text(preview="oi", created_at="2026-10-17T12:01:00Z"))
    cycle(cfg, api, FakeHarness('{"reply": "Olá!"}'), T0 + timedelta(minutes=1))
    mark_takeover(ws, ANA, "2026-10-17T12:05:00Z")  # a person answered from the phone
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=20))
    assert load_state(ws, ANA).state is ContactState.PAUSED
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=40))  # 35 minutes without a person
    assert load_state(ws, ANA).state is ContactState.ACTIVE
    cycle(cfg, api, FakeHarness(), T0 + timedelta(minutes=41))
    assert any("voltou para o atendente virtual" in s[2] for s in api.sends)
