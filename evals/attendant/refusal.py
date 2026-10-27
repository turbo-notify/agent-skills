"""What the runner does with a model answer the helper refuses, on the real model.

The real model almost never answers with something the parser rejects, so this check makes it
happen: the child process runs for real and its answer is cut in half before the runner reads it,
which is no longer a JSON object. The rule under test (``runner._refused``): the first refusal is
asked again at the next cycle, a second one in a row sends the customer the default hand-off in
the language they wrote in, and the people are told.

The check is not about what the model says, so it is not part of AC23 or AC24. It is kept beside
them so that the same ``claude`` command and the same runner cycle show the rule working.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from evals.attendant.cases import Shop
from evals.attendant.harness import (
    Recorder,
    Session,
    TurnResult,
    strip_disclosure,
    temp_base,
)
from tn_assist.i18n import Lang, t
from tn_assist.profiles import Child
from tn_assist.runner import MAX_REJECTIONS, run_child

OK = "ok"
MISMATCH = "mismatch"


@dataclass(frozen=True)
class Scenario:
    id: str
    #: How many answers in a row are cut before one is let through.
    refused: int
    #: The language the customer writes in; the default hand-off must come back in it.
    lang: Lang
    name: str
    number: str
    message: str


SCENARIOS = (
    Scenario(
        "refused-once", 1, "pt", "Marina Castro", "5511977770901", "Oi! Vocês abrem no sábado?"
    ),
    Scenario(
        "refused-twice-en", 2, "en", "Emily Stone", "5511977770902",
        "Hi! What time do you open on Saturday?",
    ),
    Scenario(
        "refused-twice-pt", 2, "pt", "Lúcia Ramos", "5511977770903",
        "Boa tarde, qual o prazo de troca?",
    ),
)  # fmt: skip


def cut(run: Callable[..., str], times: int) -> Callable[..., str]:
    """A ``spawn`` that runs the real child and cuts the answers of its first ``times`` calls."""
    calls = {"n": 0}

    def spawn(child: Child, timeout: float) -> str:
        output = run(child, timeout=timeout)
        calls["n"] += 1
        return output[: max(1, len(output) // 2)] if calls["n"] <= times else output

    return spawn


def score(scenario: Scenario, turn: TurnResult) -> dict[str, Any]:
    """The record of one scenario: what happened, and whether it is what the rule says."""
    problems: list[str] = []
    handed_over = scenario.refused >= MAX_REJECTIONS
    # every try is one cycle and one model call: the answers that are cut, then (when the rule has
    # not given up yet) the one that is let through
    tries = MAX_REJECTIONS if handed_over else scenario.refused + 1
    expected_errors = ["InvalidDecision"] * min(scenario.refused, MAX_REJECTIONS)
    if turn.errors != expected_errors:
        problems.append(f"refusals seen: {turn.errors}, expected {expected_errors}")
    if len(turn.calls) != tries:
        problems.append(f"{len(turn.calls)} model calls, expected {tries}")
    if turn.attempts != tries:
        problems.append(f"answered after {turn.attempts} cycles, expected {tries}")
    # the pack of the second try ends with one line naming the rule the first answer broke
    retry = "Your previous answer to this message was refused because "
    if turn.calls and retry in turn.calls[0].prompt:
        problems.append("the first pack already carried the line about a refused answer")
    if len(turn.calls) > 1:
        last = turn.calls[1].prompt.rstrip().splitlines()[-1]
        if not last.startswith(retry):
            problems.append("the pack of the second try does not end with the line naming the rule")
    default = t("handoff_default", scenario.lang)
    sent = strip_disclosure(turn.delivered[0]) if turn.delivered else ""
    if handed_over:
        if sent != default:
            problems.append(
                f"the customer got {sent!r}, not the default hand-off in {scenario.lang}"
            )
        if not turn.escalated:
            problems.append("the customer was not handed to a person")
        if not turn.notices:
            problems.append("the people were not told")
        if turn.state_language != scenario.lang:
            problems.append(f"the language kept is {turn.state_language}, not {scenario.lang}")
    else:
        if not sent or sent == default:
            problems.append("the second try was not delivered")
        if turn.escalated:
            problems.append("a single refusal handed the customer to a person")
    return {
        "id": scenario.id,
        "lang": scenario.lang,
        "message": scenario.message,
        "refused": scenario.refused,
        "errors": turn.errors,
        "model_calls": len(turn.calls),
        "attempts": turn.attempts,
        "elapsed_s": round(sum(c.elapsed_s for c in turn.calls), 1),
        "delivered": turn.delivered,
        "notices": len(turn.notices),
        "escalated": turn.escalated,
        "state": turn.state,
        "state_language": turn.state_language,
        "decision_error": turn.decision_error,
        "problems": problems,
        "verdict": OK if not problems else MISMATCH,
    }


def run_scenario(
    scenario: Scenario,
    shop: Shop,
    *,
    timeout: float,
    model: Optional[str],
    env: dict[str, str],
    run: Callable[..., str] = run_child,
) -> dict[str, Any]:
    with temp_base() as base:
        recorder = Recorder(timeout, run=cut(run, scenario.refused))
        session = Session(shop, Path(base), recorder=recorder, model=model, env=env)
        turn = session.say(scenario.number, scenario.name, scenario.message)
    return score(scenario, turn)
