"""The offers check: an ok that accepts an offer is never left without an answer.

The live check seeds the customer's history so that the assistant's last line is an offer, or only
"anything else?", and plays one short message on the real model. Here the model is a function, so
the scenarios, their scoring and the report section are proven without starting one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.attendant import offers, refusal, report
from evals.attendant.cases import load_cases, load_conversations, load_shop
from tn_assist.closing import is_closing

ENV = {"HOME": "/home/x", "PATH": "/usr/bin", "TURBO_NOTIFY_API_KEY": "tn_live_" + "k" * 32}
SILENCE = json.dumps({"reply": None, "facts": [], "escalate": None, "no_reply_needed": True})
ANSWER = json.dumps({"reply": "Combinado!", "facts": [], "escalate": None})
HAND_OFF = json.dumps(
    {
        "reply": "Combinado! Vou pedir para alguém da equipe.",
        "facts": [],
        "escalate": {"reason": "aceitou a oferta", "summary": "quer ajuda", "urgency": "low"},
    }
)


def play(scenario: offers.Scenario, answer: str, seen: list[str] | None = None) -> dict[str, Any]:
    def run(child: Any, timeout: float) -> str:
        if seen is not None:
            seen.append(child.stdin)
        return answer

    return offers.run_scenario(scenario, load_shop(), timeout=30.0, model=None, env=ENV, run=run)


def scenario(sid: str) -> offers.Scenario:
    return next(s for s in offers.SCENARIOS if s.id == sid)


def test_the_scenarios_are_distinct_invented_customers() -> None:
    numbers = [s.number for s in offers.SCENARIOS]
    taken = (
        {c.number for c in load_cases()}
        | {c.number for c in load_conversations()}
        | {s.number for s in refusal.SCENARIOS}
    )
    assert len(set(numbers)) == len(numbers) and not set(numbers) & taken
    assert len({s.id for s in offers.SCENARIOS}) == len(offers.SCENARIOS)
    assert {s.lang for s in offers.SCENARIOS} == {"pt", "en"}


def test_both_sides_are_covered_in_both_languages() -> None:
    owed_silence = [s for s in offers.SCENARIOS if s.silent]
    owed_an_answer = [s for s in offers.SCENARIOS if not s.silent]
    assert owed_silence and owed_an_answer
    for side in (owed_silence, owed_an_answer):
        assert {s.lang for s in side} == {"pt", "en"}
    # a thanks after "anything else?" is something the guard would let through; the answer to an
    # offer includes words ("sim", "pode ser") the guard would never silence
    assert all(is_closing(s.message) for s in owed_silence)
    assert any(not is_closing(s.message) for s in owed_an_answer)


def test_the_assistants_offer_is_in_the_pack_the_model_reads() -> None:
    seen: list[str] = []
    play(scenario("offer-pt-ok"), ANSWER, seen)
    assert "Quer que eu faça isso?" in seen[0]
    assert "Vocês têm abajur infantil?" in seen[0]


def test_an_ok_that_accepts_an_offer_is_owed_an_answer_and_silence_is_a_mismatch() -> None:
    thumb = scenario("offer-pt-thumb")
    silent = play(thumb, SILENCE)
    assert silent["verdict"] == "mismatch" and silent["silenced"] is True
    # the guard lets a bare thumbs up through, so the model's flag is all that stands in the way
    assert any("left without any answer" in p for p in silent["problems"])
    assert play(thumb, ANSWER)["verdict"] == "ok"
    handed = play(thumb, HAND_OFF)
    assert handed["verdict"] == "ok" and handed["escalated"] is True


def test_an_ok_that_accepts_an_offer_with_words_the_guard_refuses_still_gets_a_hand_off() -> None:
    record = play(scenario("offer-pt-sim"), SILENCE)  # "sim" is not a closing: refused, handed over
    assert record["silenced"] is False and record["escalated"] is True
    assert record["verdict"] == "ok"  # the guard caught what the model got wrong


def test_a_thanks_after_anything_else_is_owed_silence_and_an_answer_is_a_mismatch() -> None:
    thanks = scenario("else-pt-obrigado")
    assert play(thanks, SILENCE)["verdict"] == "ok"
    answered = play(thanks, ANSWER)
    assert answered["verdict"] == "mismatch" and "not left alone" in str(answered["problems"])
    handed = play(thanks, HAND_OFF)
    assert handed["verdict"] == "mismatch"


def test_the_report_counts_the_offers_and_they_decide_the_run(tmp_path: Path) -> None:
    good = {"id": "a", "verdict": "ok", "problems": [], "delivered": ["x"], "message": "m"}
    bad = {**good, "id": "b", "verdict": "mismatch", "problems": ["no"], "owed": "silence"}
    rows = [{**r, "owed": "silence", "silenced": False} for r in (good, bad)]
    (tmp_path / "cases.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "turns.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "offers.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    summary = report.write_reports(tmp_path, {})
    assert summary["offers"]["total"] == 2
    assert summary["offers"]["final"] == {"pass": 1, "fail": 1, "pending": 0}
    assert not report.is_green(summary)
    assert "[mismatch] b" in (tmp_path / "review.txt").read_text(encoding="utf-8")
    assert "a [ok]" in (tmp_path / "replies.txt").read_text(encoding="utf-8")
