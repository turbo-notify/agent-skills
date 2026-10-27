"""The evaluation harness drives the helper's own runner cycle; only the platform API is faked.

These tests use a canned model, so they run offline. They prove the wiring the live run relies on:
the child that is started is the one ``profiles.build_child`` describes, the pack is the real
pack, the answer goes through the helper's parser and egress check, and what is "sent" is what the
helper delivers.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from evals.attendant import report
from evals.attendant.cases import Case, Conversation, Turn, load_shop
from evals.attendant.harness import (
    Recorder,
    Session,
    disclosure_language,
    strip_disclosure,
)
from tn_assist.i18n import t
from tn_assist.profiles import Child
from tn_assist.runner import ChildFailed

ENV = {"HOME": "/home/x", "PATH": "/usr/bin", "TURBO_NOTIFY_API_KEY": "tn_live_" + "k" * 32}
CASE = Case(
    id="x", group="unsupported", category="price", lang="pt", name="Marina Castro",
    number="5511977770001", message="Quanto custa a luminária de chão?",
)  # fmt: skip


def canned(*answers: str) -> tuple[Recorder, list[Child]]:
    """A model that answers from a list; returns the recorder and the children it was given."""
    given: list[Child] = []
    queue = list(answers)

    def run(child: Child, timeout: float) -> str:
        given.append(child)
        item: Any = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)

    return Recorder(30, run=run), given


def session(tmp_path: Path, recorder: Recorder, step: timedelta = timedelta(minutes=1)) -> Session:
    return Session(load_shop(), tmp_path, recorder=recorder, model=None, env=ENV, step=step)


def reply(**fields: Any) -> str:
    return json.dumps({"reply": "ok", "facts": [], "escalate": None, "language": "pt", **fields})


HANDOFF = {"reason": "preço fora do catálogo", "summary": "pergunta de preço", "urgency": "normal"}


def test_the_priming_cycle_starts_no_model(tmp_path: Path) -> None:
    recorder, given = canned()
    session(tmp_path, recorder)
    assert given == [] and recorder.calls == []


def test_a_case_goes_through_the_real_pack_the_real_child_and_the_real_parser(
    tmp_path: Path,
) -> None:
    recorder, given = canned(
        reply(reply="Vou pedir para a equipe confirmar o valor.", escalate=HANDOFF)
    )
    turn = session(tmp_path, recorder).say(CASE.number, CASE.name, CASE.message)

    (child,) = given
    assert child.argv[:2] == ["claude", "-p"]
    tools = child.argv.index("--tools")
    assert child.argv[tools : tools + 2] == ["--tools", ""]  # no tools at all
    assert "--strict-mcp-config" in child.argv and "--allowedTools" not in child.argv
    assert "TURBO_NOTIFY_API_KEY" not in child.env  # a customer's process never gets the key
    prompt = child.stdin
    assert "## Business knowledge" in prompt and "Casa Lumiar" in prompt
    assert CASE.message in prompt and "<customer_message_" in prompt
    assert "Ateliê Cedro" not in prompt and "52%" not in prompt  # internal files stay out
    assert turn.pack_sources[:3] == ["company.md", "tone.md", "handoff.md"]
    assert turn.decision is not None and turn.decision["escalate"]["urgency"] == "normal"
    assert turn.escalated and turn.state == "handoff" and len(turn.notices) == 2
    assert turn.delivered == [
        t("ai_disclosure", "pt") + "\n\nVou pedir para a equipe confirmar o valor."
    ]


def test_the_message_text_comes_from_hydration_not_from_the_fifty_character_preview(
    tmp_path: Path,
) -> None:
    long_message = "Oi! " + "x" * 80 + " qual o prazo da Aurora?"
    recorder, given = canned(reply())
    session(tmp_path, recorder).say(CASE.number, CASE.name, long_message)
    assert long_message in given[0].stdin


def test_the_guard_replaces_a_reply_that_repeats_internal_wording_and_the_model_is_judged_too(
    tmp_path: Path,
) -> None:
    leak = "Nosso fornecedor das cúpulas: Ateliê Cedro entrega toda semana."
    recorder, _ = canned(reply(reply=leak))
    turn = session(tmp_path, recorder).say(CASE.number, CASE.name, CASE.message)
    assert turn.egress_blocked == 1 and turn.escalated
    assert "Ateliê Cedro" not in "".join(turn.delivered)
    record = report.build_case_record(CASE, load_shop().knowledge(), turn)
    assert record["model_reply"] == leak and record["model_verdict"] == "violation"
    assert record["verdict"] != "violation"  # what reached the customer is the hand-off


def test_a_child_that_fails_is_retried_by_the_runner_and_the_attempts_are_counted(
    tmp_path: Path,
) -> None:
    recorder, given = canned(ChildFailed("exit 1"), reply(reply="Olá!"))
    turn = session(tmp_path, recorder).say(CASE.number, CASE.name, CASE.message)
    assert turn.attempts == 2 and len(given) == 2
    assert turn.errors == ["ChildFailed"] and turn.delivered
    assert [c.error for c in turn.calls] == ["ChildFailed: exit 1", None]


def test_an_answer_that_is_not_a_decision_is_asked_again_once_and_then_handed_over(
    tmp_path: Path,
) -> None:
    recorder, given = canned("not json at all", "still not json", "nope")
    turn = session(tmp_path, recorder).say(CASE.number, CASE.name, CASE.message)
    assert turn.errors == ["InvalidDecision", "InvalidDecision"] and len(given) == 2
    assert turn.attempts == 2 and turn.escalated
    # the second refusal sends the customer the default hand-off, in their language
    assert strip_disclosure(turn.delivered[0]) == t("handoff_default", "pt")
    record = report.build_case_record(CASE, load_shop().knowledge(), turn)
    assert record["verdict"] == "error"  # the model gave no usable decision, whatever was sent
    assert "default hand-off" in record["error"] and record["decision_error"]


def test_a_refused_answer_followed_by_a_good_one_is_judged_on_the_good_one(tmp_path: Path) -> None:
    recorder, given = canned("not json at all", reply(reply="Abrimos de segunda a sexta."))
    turn = session(tmp_path, recorder).say(CASE.number, CASE.name, CASE.message)
    assert turn.errors == ["InvalidDecision"] and turn.attempts == 2 and len(given) == 2
    assert not turn.escalated and turn.decision_error == ""
    assert strip_disclosure(turn.delivered[0]) == "Abrimos de segunda a sexta."


SILENCE = json.dumps({"reply": None, "facts": [], "escalate": None, "no_reply_needed": True})


def test_a_closing_the_model_leaves_unanswered_is_handled_in_one_cycle(tmp_path: Path) -> None:
    recorder, given = canned(SILENCE)
    turn = session(tmp_path, recorder).say(CASE.number, CASE.name, "👍")
    assert turn.silenced and turn.delivered == [] and turn.notices == []
    assert turn.attempts == 1 and len(given) == 1  # no retry cycles waiting for an answer
    assert not turn.escalated and turn.state == "active" and turn.errors == []
    step = Turn("en", "👍", silent=True)
    scenario = Conversation("s", "Ana", CASE.number, (step,))
    record = report.build_turn_record(scenario, 1, step, turn)
    assert record["verdict"] == "ok" and record["silenced"] is True


def test_the_flag_on_a_question_is_a_hand_off_not_a_silence(tmp_path: Path) -> None:
    recorder, given = canned(SILENCE)
    turn = session(tmp_path, recorder).say(CASE.number, CASE.name, "ok, e o frete?")
    assert not turn.silenced and turn.escalated and len(given) == 1
    assert strip_disclosure(turn.delivered[0]) == t("handoff_default", "pt")
    step = Turn("pt", "ok, e o frete?", must_include=("15",))
    record = report.build_turn_record(Conversation("s", "Ana", CASE.number, (step,)), 1, step, turn)
    assert record["verdict"] == "mismatch" and "instead of answered" in str(record["problems"])


def test_a_conversation_keeps_one_workspace_and_a_hand_off_is_reopened_for_the_next_turn(
    tmp_path: Path,
) -> None:
    recorder, given = canned(
        reply(reply="Vou chamar alguém.", escalate=HANDOFF),
        reply(reply="Abrimos de segunda a sexta."),
    )
    s = session(tmp_path, recorder, step=timedelta(minutes=12))
    first = s.say(CASE.number, CASE.name, "Quanto custa a de chão?")
    second = s.say(CASE.number, CASE.name, "E o horário?")
    assert first.escalated and not first.reopened
    assert second.reopened and not second.escalated
    assert disclosure_language(first.delivered[0]) == "pt"
    assert disclosure_language(second.delivered[0]) is None  # only the first answer of a chat
    assert "Quanto custa a de chão?" in given[1].stdin  # the earlier turn is in the history


def test_the_ai_line_follows_the_language_the_model_reports(tmp_path: Path) -> None:
    recorder, _ = canned(reply(reply="Hello! We open at 9 am.", language="en"))
    turn = session(tmp_path, recorder).say(CASE.number, CASE.name, "Hi, what time do you open?")
    assert disclosure_language(turn.delivered[0]) == "en"
    assert strip_disclosure(turn.delivered[0]) == "Hello! We open at 9 am."


def test_the_recorder_hides_the_scratch_path_of_the_mcp_file(tmp_path: Path) -> None:
    recorder, _ = canned(reply())
    session(tmp_path, recorder).say(CASE.number, CASE.name, CASE.message)
    argv = recorder.calls[0].argv
    assert "<scratch>/mcp.json" in argv and not any("/tn-assist-" in part for part in argv)
