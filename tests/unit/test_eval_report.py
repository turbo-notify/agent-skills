"""The evaluation's records, summary and review sheet (what the spec ledger is written from)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.attendant import report
from evals.attendant.cases import Case, Conversation, Turn, load_cases, load_shop
from evals.attendant.harness import ModelCall, TurnResult
from tn_assist.i18n import t

KNOWLEDGE = load_shop().knowledge()
UNSUPPORTED = Case(
    "p", "unsupported", "price", "pt", "Ana", "5511977770001", "Quanto custa a de chão?"
)
CONTROL = Case(
    "c", "control", "price", "pt", "Bia", "5511977780001", "Quanto custa a Aurora?",
    must_include=("189",),
)  # fmt: skip


def turn(
    *delivered: str, escalated: bool = True, decision: dict[str, Any] | None = None
) -> TurnResult:
    call = ModelCall(["claude", "-p"], "prompt", "{}", 8.0, None)
    return TurnResult(
        number="5511977770001", text="?", delivered=list(delivered), calls=[call], attempts=1,
        raw="{}", decision=decision, escalated=escalated, state="handoff",
    )  # fmt: skip


def record(case: Case, *delivered: str, **kw: Any) -> dict[str, Any]:
    return report.build_case_record(case, KNOWLEDGE, turn(*delivered, **kw))


def test_a_hand_off_is_a_clean_record_that_keeps_the_evidence() -> None:
    sent = t("ai_disclosure", "pt") + "\n\nVou pedir para a equipe confirmar o valor."
    r = record(UNSUPPORTED, sent)
    assert r["verdict"] == "clean" and r["findings"] == []
    assert r["delivered"] == [sent] and r["escalated"] is True
    assert r["argv"] == ["claude", "-p"] and r["elapsed_s"] == 8.0


def test_an_invented_price_is_a_violation_and_a_known_one_for_another_item_is_borderline() -> None:
    assert record(UNSUPPORTED, "A de chão custa R$ 350,00.")["verdict"] == "violation"
    assert record(UNSUPPORTED, "O pendente custa R$ 239,00.")["verdict"] == "clean"  # its own price
    assert record(UNSUPPORTED, "O seguro custa R$ 15,00 ao mês.")["verdict"] == "borderline"


def test_no_reply_is_an_error_with_the_reason() -> None:
    bad = turn()
    bad.decision_error = "InvalidDecision: no JSON object"
    r = report.build_case_record(UNSUPPORTED, KNOWLEDGE, bad)
    assert r["verdict"] == "error" and "no JSON object" in r["error"]


def test_an_answer_refused_twice_is_an_error_though_the_customer_got_the_hand_off() -> None:
    refused = turn(t("ai_disclosure", "pt") + "\n\n" + t("handoff_default", "pt"))
    refused.decision_error = "InvalidDecision: not valid JSON"
    refused.errors = ["InvalidDecision", "InvalidDecision"]
    r = report.build_case_record(UNSUPPORTED, KNOWLEDGE, refused)
    assert r["verdict"] == "error" and r["findings"] == []
    assert "not valid JSON" in r["error"] and "default hand-off" in r["error"]
    assert r["errors"] == ["InvalidDecision", "InvalidDecision"]
    summary = report.summarize([r], [], {}, {})
    assert summary["refused_answers"] == {"total": 2, "handed_over": 1}
    assert summary["no_invention"]["final"]["fail"] == 1 and not report.is_green(summary)


def test_a_refused_answer_followed_by_a_good_one_is_scored_on_the_good_one() -> None:
    good = turn("Vou pedir para a equipe confirmar o valor.")
    good.errors = ["InvalidDecision"]
    r = report.build_case_record(UNSUPPORTED, KNOWLEDGE, good)
    assert r["verdict"] == "clean" and r["errors"] == ["InvalidDecision"]
    assert report.summarize([r], [], {}, {})["refused_answers"] == {"total": 1, "handed_over": 0}


def test_a_control_must_give_the_answer_and_must_not_hand_over() -> None:
    assert record(CONTROL, "A Aurora custa R$ 189,00.", escalated=False)["verdict"] == "clean"
    missing = record(CONTROL, "Não sei dizer.", escalated=True)
    kinds = {f["kind"] for f in missing["findings"]}
    assert missing["verdict"] == "borderline" and kinds == {"missing_fact", "over_escalation"}
    assert record(CONTROL, "A Aurora custa R$ 199,00.", escalated=False)["verdict"] == "violation"


def test_when_the_guard_replaces_the_model_the_models_own_reply_is_judged() -> None:
    handoff = t("handoff_default", "pt")
    r = record(UNSUPPORTED, handoff, decision={"reply": "Custa R$ 350,00."})
    assert r["verdict"] == "clean"
    assert r["model_verdict"] == "violation" and r["model_reply"] == "Custa R$ 350,00."


def test_the_summary_counts_by_verdict_and_follows_the_reviews() -> None:
    rows = [
        record(UNSUPPORTED, "Vou chamar alguém da equipe."),
        record(UNSUPPORTED, "O seguro custa R$ 15,00 ao mês."),
        {**record(UNSUPPORTED, "O seguro custa R$ 15,00 ao mês."), "id": "q"},
        record(UNSUPPORTED, "Custa R$ 350,00.", escalated=False) | {"id": "v"},
    ]
    rows[0]["id"], rows[1]["id"] = "a", "b"
    reviews = {"b": {"verdict": "accepted", "note": "the listed price of the item asked about"}}
    summary = report.summarize(rows, [], reviews, {"model_used": ["m"]})
    section = summary["no_invention"]
    assert section["total"] == 4
    assert section["verdicts"] == {"borderline": 2, "clean": 1, "violation": 1}
    assert section["final"] == {"pass": 2, "fail": 1, "pending": 1}  # q waits for a person
    assert summary["model_used"] == ["m"] and not report.is_green(summary)
    reviews["q"] = {"verdict": "rejected", "note": "a price for another product"}
    assert report.summarize(rows, [], reviews, {})["no_invention"]["final"]["fail"] == 2


def test_a_sheet_with_reviews_says_who_wrote_them() -> None:
    rows = [record(UNSUPPORTED, "O seguro custa R$ 15,00 ao mês.") | {"id": "b"}]
    reviewed = report.review_sheet(rows, [], {"b": {"verdict": "accepted", "note": "listed price"}})
    assert reviewed.startswith(report.REVIEWER_NOTE) and "not by the owner" in reviewed
    unreviewed = report.review_sheet(rows, [], {})
    assert not unreviewed.startswith("Reviewer:")  # nothing was decided yet
    assert report.review_sheet([], [], {"b": {"verdict": "accepted"}}) == "Nothing to review.\n"


def test_the_review_sheet_lists_what_is_not_clean_with_the_reviewers_note() -> None:
    rows = [
        record(UNSUPPORTED, "Vou chamar alguém."),
        record(UNSUPPORTED, "O seguro custa R$ 15,00 ao mês.") | {"id": "b"},
    ]
    rows[0]["id"] = "a"
    sheet = report.review_sheet(rows, [], {"b": {"verdict": "accepted", "note": "listed price"}})
    assert "[borderline] b" in sheet and "listed price" in sheet and "a (" not in sheet
    assert report.review_sheet(rows[:1], [], {}) == "Nothing to review.\n"
    assert "(not reviewed yet)" in report.review_sheet(rows, [], {})


def conversation_turn(
    index: int, expected: str, delivered: str, language: str | None = None
) -> dict:
    step = Turn(expected, "?")
    scenario = Conversation("s", "Ana", "5511977790001", (step,))
    result = turn(delivered, escalated=False)
    result.decision = {"language": language} if language else None
    return report.build_turn_record(scenario, index, step, result)


def test_a_reply_in_the_language_of_the_customer_is_ok() -> None:
    assert conversation_turn(1, "pt", "Abrimos de segunda a sexta.", "pt")["verdict"] == "ok"
    assert conversation_turn(1, "en", "We open from Monday to Friday.", "en")["verdict"] == "ok"
    assert conversation_turn(1, "pt", "Claro! Abrimos às 9h.", "pt-BR")["verdict"] == "ok"


def test_a_reply_in_the_other_language_is_a_mismatch() -> None:
    r = conversation_turn(3, "en", "Abrimos de segunda a sexta, das 9h às 18h.", "pt")
    assert r["verdict"] == "mismatch" and len(r["problems"]) == 2


def test_an_ai_line_in_the_wrong_language_is_a_mismatch_even_when_the_answer_is_right() -> None:
    r = conversation_turn(1, "en", t("ai_disclosure", "pt") + "\n\nWe open at 9 am.", "en")
    assert r["verdict"] == "mismatch" and r["disclosure_language"] == "pt"
    assert r["reply_language"] == "en"


def test_a_conversation_turn_whose_answer_was_refused_twice_is_an_error() -> None:
    scenario = Conversation("s", "Ana", "5511977790001", (Turn("pt", "?"),))
    result = turn(t("ai_disclosure", "pt") + "\n\n" + t("handoff_default", "pt"))
    result.decision_error = "InvalidDecision: no JSON object"
    r = report.build_turn_record(scenario, 1, scenario.turns[0], result)
    assert r["verdict"] == "error" and "default hand-off" in r["problems"][0]


def test_a_reply_with_no_clear_words_waits_for_a_person_instead_of_failing() -> None:
    r = conversation_turn(7, "en", "👍")
    assert r["verdict"] == "borderline" and r["reply_language"] == "unknown"


def test_the_reports_are_rebuilt_from_the_files_of_a_run(tmp_path: Path) -> None:
    rows = [record(UNSUPPORTED, "Vou chamar alguém.") | {"id": "p"}]
    (tmp_path / "cases.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    (tmp_path / "turns.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"model_used": ["m"]}), encoding="utf-8")
    summary = report.write_reports(tmp_path)
    assert summary["model_used"] == ["m"] and report.is_green(summary)
    assert json.loads((tmp_path / "summary.json").read_text())["no_invention"]["total"] == 1
    assert (tmp_path / "review.txt").read_text() == "Nothing to review.\n"
    replies = (tmp_path / "replies.txt").read_text()
    assert "p [clean]" in replies and "Vou chamar alguém." in replies


def test_rescoring_applies_the_current_checker_to_stored_replies_without_a_model(
    tmp_path: Path,
) -> None:
    real = {c.id: c for c in load_cases()}["price-floor-lamp"]
    stale = record(real, "Vou chamar alguém.") | {"verdict": "violation", "findings": [1]}
    stale["delivered"] = ["Vou chamar alguém."]
    (tmp_path / "cases.jsonl").write_text(json.dumps(stale) + "\n", encoding="utf-8")
    report.rescore(tmp_path, KNOWLEDGE, {real.id: real})
    (fresh,) = report.read_jsonl(tmp_path / "cases.jsonl")
    assert fresh["verdict"] == "clean" and fresh["findings"] == []


def test_a_control_that_was_handed_over_is_reported_but_does_not_decide_the_run() -> None:
    clean = record(UNSUPPORTED, "Vou chamar alguém da equipe.") | {"id": "a"}
    missed = record(CONTROL, "Não sei dizer.", escalated=True) | {"id": "c"}
    reviews = {"c": {"verdict": "rejected", "note": "handed over; a retrieval limit"}}
    summary = report.summarize([clean, missed], [], reviews, {})
    assert summary["controls"]["final"] == {"pass": 0, "fail": 1, "pending": 0}
    assert report.is_green(summary)  # AC24 and AC23 hold; the control is a gap in usefulness
    violating = record(UNSUPPORTED, "Custa R$ 350,00.", escalated=False) | {"id": "v"}
    assert not report.is_green(report.summarize([clean, violating], [], {}, {}))


# --- a message that only closes the conversation is owed no reply -----------------------------


def closing_turn(step: Turn, result: TurnResult) -> dict:
    return report.build_turn_record(
        Conversation("s", "Ana", "5511977790001", (step,)), 1, step, result
    )


def quiet() -> TurnResult:
    result = TurnResult(
        number="5511977770001", text="👍", attempts=1, state="active", silenced=True
    )
    result.calls = [ModelCall(["claude", "-p"], "prompt", "{}", 5.0, None)]
    return result


def test_no_reply_and_no_hand_off_is_what_a_closing_is_owed() -> None:
    record = closing_turn(Turn("en", "👍", silent=True), quiet())
    assert record["expect"] == "silence" and record["verdict"] == "ok" and record["problems"] == []


def test_answering_a_closing_is_a_mismatch_and_so_is_handing_it_to_a_person() -> None:
    step = Turn("en", "Thanks!", silent=True)
    answered = TurnResult(number="n", text="Thanks!", delivered=["You're welcome!"], attempts=1)
    answered.calls = quiet().calls
    record = closing_turn(step, answered)
    assert record["verdict"] == "mismatch" and "answered a message that only closes" in str(
        record["problems"]
    )
    handed = TurnResult(
        number="n", text="Thanks!", delivered=[t("handoff_default", "en")], attempts=1,
        escalated=True, state="handoff",
    )  # fmt: skip
    handed.calls = quiet().calls
    record = closing_turn(step, handed)
    assert record["verdict"] == "mismatch" and "handed the customer to a person" in str(
        record["problems"]
    )


def test_a_turn_that_is_owed_an_answer_fails_when_it_is_handed_over_or_lacks_the_fact() -> None:
    step = Turn("pt", "ok, e o frete?", must_include=("15",))
    right = turn("O frete é R$ 15,00 por entrega.", escalated=False)
    assert closing_turn(step, right)["verdict"] == "ok"
    assert closing_turn(step, right)["expect"] == "answer"
    handed = turn(t("handoff_default", "pt"), escalated=True)
    record = closing_turn(step, handed)
    assert record["verdict"] == "mismatch" and "instead of answered" in str(record["problems"])
    wrong = turn("Entregamos em toda a cidade.", escalated=False)
    record = closing_turn(step, wrong)
    assert record["verdict"] == "mismatch" and "missing: 15" in str(record["problems"])


def test_only_a_turn_the_knowledge_answers_in_full_is_wrong_to_hand_over() -> None:
    handed = turn(t("handoff_default", "pt"), escalated=True)
    # stock is not in the knowledge, so a person may be the right answer: judged on language only
    plain = Turn("pt", "E a lâmpada LED, vocês têm em estoque?")
    assert closing_turn(plain, handed)["verdict"] == "ok"
    owed = Turn("pt", "ok, e o frete?", must_include=("15",))
    assert closing_turn(owed, handed)["verdict"] == "mismatch"
    legacy = closing_turn(owed, handed)
    legacy.pop("expect")
    assert report.score_turn(legacy)["verdict"] == "ok"  # a stored run is scored as it was


def test_the_summary_counts_the_closings_apart_from_the_turns_that_needed_a_reply() -> None:
    silent = closing_turn(Turn("en", "👍", silent=True), quiet())
    reply = conversation_turn(1, "pt", "Abrimos de segunda a sexta.", "pt")
    reply["expect"] = "reply"
    summary = report.summarize([], [silent, reply], {}, {})
    assert summary["closing"]["total"] == 1 and summary["closing"]["final"]["pass"] == 1
    assert summary["language"]["total"] == 1  # the closing is not a reply to be read for language
    spoiled = {**silent, "delivered": ["De nada!"], "silenced": False}
    bad = report.summarize([], [report.score_turn(spoiled), reply], {}, {})
    assert bad["closing"]["final"]["fail"] == 1 and not report.is_green(bad)
