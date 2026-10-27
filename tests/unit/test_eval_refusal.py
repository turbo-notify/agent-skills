"""The refusal check: the runner's rule for an answer the helper refuses, with a canned model.

The live check cuts the real model's answers in half. Here the model is a function, so the
scenarios, their scoring and the report section are proven without starting one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals.attendant import refusal, report
from evals.attendant.cases import load_cases, load_conversations, load_shop
from evals.attendant.harness import ModelCall, TurnResult
from tn_assist.i18n import t

ENV = {"HOME": "/home/x", "PATH": "/usr/bin", "TURBO_NOTIFY_API_KEY": "tn_live_" + "k" * 32}


def model_that_answers(reply: str) -> Any:
    answer = json.dumps(
        {"reply": reply, "facts": [], "escalate": None, "language": "pt", "state": None}
    )

    def run(child: Any, timeout: float) -> str:
        return answer

    return run


def play(scenario: refusal.Scenario, tmp_path: Path) -> dict[str, Any]:
    # the base folder is the harness's own temporary one; tmp_path only keeps pytest tidy
    return refusal.run_scenario(
        scenario, load_shop(), timeout=30.0, model=None, env=ENV, run=model_that_answers("Abrimos!")
    )


def test_the_first_answers_are_cut_and_the_later_ones_pass_through() -> None:
    spawn = refusal.cut(model_that_answers("0123456789"), 2)
    cut_once, cut_twice, whole = (spawn(None, 1.0) for _ in range(3))
    assert json.loads(whole)["reply"] == "0123456789"
    assert whole.startswith(cut_once) and cut_once == cut_twice and len(cut_once) < len(whole)
    for broken in (cut_once, cut_twice):
        with pytest.raises(ValueError):
            json.loads(broken)


def test_an_answer_refused_once_is_asked_again_and_delivered(tmp_path: Path) -> None:
    record = play(refusal.SCENARIOS[0], tmp_path)
    assert record["verdict"] == "ok", record["problems"]
    assert record["errors"] == ["InvalidDecision"] and record["attempts"] == 2
    assert record["escalated"] is False and "Abrimos!" in record["delivered"][0]


@pytest.mark.parametrize("scenario", refusal.SCENARIOS[1:], ids=lambda s: s.id)
def test_two_refusals_in_a_row_send_the_default_hand_off_in_the_customers_language(
    scenario: refusal.Scenario, tmp_path: Path
) -> None:
    record = play(scenario, tmp_path)
    assert record["verdict"] == "ok", record["problems"]
    assert record["errors"] == ["InvalidDecision", "InvalidDecision"]
    assert record["model_calls"] == 2 and record["escalated"] is True and record["notices"]
    assert t("handoff_default", scenario.lang) in record["delivered"][0]
    assert record["state_language"] == scenario.lang


def turn(**kw: Any) -> TurnResult:
    call = ModelCall(["claude", "-p"], "prompt", "{", 5.0, None)
    base: dict[str, Any] = {"number": "5511977770902", "text": "?", "attempts": 2}
    return TurnResult(**{**base, "calls": [call, call], **kw})


def test_a_hand_off_in_the_wrong_language_is_a_mismatch() -> None:
    english = refusal.SCENARIOS[1]
    wrong = turn(
        delivered=[t("handoff_default", "pt")],
        errors=["InvalidDecision"] * 2,
        escalated=True,
        notices=["notice"],
        state_language="pt",
    )
    record = refusal.score(english, wrong)
    assert record["verdict"] == "mismatch"
    assert any("default hand-off in en" in p for p in record["problems"])
    assert any("language kept" in p for p in record["problems"])


def test_a_single_refusal_that_hands_the_customer_over_is_a_mismatch() -> None:
    once = refusal.SCENARIOS[0]
    early = turn(
        delivered=[t("handoff_default", "pt")],
        errors=["InvalidDecision"],
        escalated=True,
    )
    problems = refusal.score(once, early)["problems"]
    assert any("a single refusal" in p for p in problems)
    assert any("second try was not delivered" in p for p in problems)


def test_the_scenarios_are_distinct_invented_customers() -> None:
    numbers = [s.number for s in refusal.SCENARIOS]
    taken = {c.number for c in load_cases()} | {c.number for c in load_conversations()}
    assert len(set(numbers)) == len(numbers) and not set(numbers) & taken
    assert len({s.id for s in refusal.SCENARIOS}) == len(refusal.SCENARIOS)
    assert {s.lang for s in refusal.SCENARIOS} == {"pt", "en"}


def test_the_report_counts_the_refusal_check_and_it_decides_the_run(tmp_path: Path) -> None:
    ok = {"id": "a", "verdict": "ok", "problems": [], "delivered": ["x"], "message": "m"}
    bad = {**ok, "id": "b", "verdict": "mismatch", "problems": ["no"], "refused": 2}
    (tmp_path / "cases.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "turns.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "refusals.jsonl").write_text(
        "".join(json.dumps({**r, "refused": 1}) + "\n" for r in (ok, bad)), encoding="utf-8"
    )
    summary = report.write_reports(tmp_path, {})
    assert summary["refusal"]["total"] == 2
    assert summary["refusal"]["final"] == {"pass": 1, "fail": 1, "pending": 0}
    assert not report.is_green(summary)
    assert "[mismatch] b" in (tmp_path / "review.txt").read_text(encoding="utf-8")
    assert "a [ok]" in (tmp_path / "replies.txt").read_text(encoding="utf-8")


def test_the_second_pack_must_end_with_the_line_naming_the_broken_rule(tmp_path: Path) -> None:
    record = play(refusal.SCENARIOS[0], tmp_path)  # the real cycle: the line is there
    assert record["verdict"] == "ok", record["problems"]
    plain = ModelCall(["claude", "-p"], "a pack\nAnswer with one JSON object", "{", 5.0, None)
    without = turn(
        delivered=["Abrimos!"], errors=["InvalidDecision"], calls=[plain, plain], attempts=2,
        escalated=False, state="active",
    )  # fmt: skip
    problems = refusal.score(refusal.SCENARIOS[0], without)["problems"]
    assert any("does not end with the line naming the rule" in p for p in problems)
    early = ModelCall(
        ["claude", "-p"], "a pack\nYour previous answer to this message was refused because x.",
        "{", 5.0, None,
    )  # fmt: skip
    problems = refusal.score(refusal.SCENARIOS[0], turn(calls=[early, early]))["problems"]
    assert any("first pack already carried" in p for p in problems)
