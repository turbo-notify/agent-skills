"""An "ok" that accepts something the assistant offered must not be left unanswered.

The guard in ``closing.py`` cannot tell a thumbs up that closes a conversation from one that says
yes to an offer ("quer que eu peça para alguém da equipe te ajudar?"). That judgment stays with the
model, and the contract tells it to reply when the message answers something it asked or offered.
This check puts the model to it on the real ``claude`` command: the customer's history is seeded so
that the assistant's last line is an offer, or only "anything else?", and then one short message
arrives. After an offer the customer is owed an answer or a hand-off, never silence. After
"anything else?" a thanks is owed silence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from evals.attendant.cases import Shop
from evals.attendant.harness import Recorder, Session, TurnResult, temp_base
from tn_assist.memory import append_line
from tn_assist.paths import ContactKey
from tn_assist.runner import run_child

OK = "ok"
MISMATCH = "mismatch"
#: Before the clock of a session, so the seeded lines are older than the new message.
SEEDED_AT = "2026-10-24T13:00:00Z"

History = tuple[tuple[str, str], ...]
OFFER_PT: History = (
    ("contact", "Vocês têm abajur infantil?"),
    (
        "assistant",
        "Não tenho essa informação aqui, mas posso pedir para alguém da equipe te ajudar. "
        "Quer que eu faça isso?",
    ),
)
OFFER_EN: History = (
    ("contact", "Do you sell kids lamps?"),
    (
        "assistant",
        "I don't have that information, but I can ask someone from the team to help you. "
        "Would you like that?",
    ),
)
ELSE_PT: History = (
    ("contact", "Quanto é o frete?"),
    ("assistant", "O frete é de R$ 15,00 por entrega. Posso ajudar com mais alguma coisa?"),
)
ELSE_EN: History = (
    ("contact", "How much is shipping?"),
    ("assistant", "Shipping is R$ 15,00 per delivery. Is there anything else I can help with?"),
)


@dataclass(frozen=True)
class Scenario:
    id: str
    lang: str
    history: History
    message: str
    #: True when the message only closes a chat: the customer is owed silence. False when it
    #: accepts an offer: the customer is owed an answer or a hand-off.
    silent: bool
    name: str
    number: str


SCENARIOS = (
    Scenario("offer-pt-thumb", "pt", OFFER_PT, "👍", False, "Cliente Um", "5511977791001"),
    Scenario("offer-pt-ok", "pt", OFFER_PT, "ok", False, "Cliente Dois", "5511977791002"),
    Scenario("offer-pt-sim", "pt", OFFER_PT, "sim", False, "Cliente Três", "5511977791003"),
    Scenario(
        "offer-pt-pode-ser", "pt", OFFER_PT, "pode ser", False, "Cliente Quatro", "5511977791004"
    ),
    Scenario("offer-en-ok", "en", OFFER_EN, "ok", False, "Customer Five", "5511977791005"),
    Scenario("offer-en-thumb", "en", OFFER_EN, "👍", False, "Customer Six", "5511977791006"),
    Scenario("else-pt-ok", "pt", ELSE_PT, "ok", True, "Cliente Sete", "5511977791007"),
    Scenario("else-pt-thumb", "pt", ELSE_PT, "👍", True, "Cliente Oito", "5511977791008"),
    Scenario("else-pt-obrigado", "pt", ELSE_PT, "obrigado", True, "Cliente Nove", "5511977791009"),
    Scenario("else-en-thanks", "en", ELSE_EN, "thanks", True, "Customer Ten", "5511977791010"),
)  # fmt: skip


def score(scenario: Scenario, turn: TurnResult) -> dict[str, Any]:
    """The record of one scenario: what the customer got, and whether that is what it was owed."""
    problems: list[str] = []
    if scenario.silent:
        if not turn.silenced:
            problems.append("a thanks after 'anything else?' was not left alone")
        if turn.delivered or turn.escalated:
            problems.append("something was sent, or the customer was handed over, for a thanks")
    else:
        if turn.silenced:
            problems.append("an ok that accepts an offer was left without any answer")
        if not turn.delivered and not turn.escalated:
            problems.append("the customer got neither an answer nor a hand-off")
    return {
        "id": scenario.id,
        "lang": scenario.lang,
        "message": scenario.message,
        "owed": "silence" if scenario.silent else "an answer or a hand-off",
        "silenced": turn.silenced,
        "escalated": turn.escalated,
        "delivered": turn.delivered,
        "attempts": turn.attempts,
        "elapsed_s": round(sum(c.elapsed_s for c in turn.calls), 1),
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
        recorder = Recorder(timeout, run=run)
        session = Session(shop, Path(base), recorder=recorder, model=model, env=env)
        key = ContactKey.parse(scenario.number)
        for index, (role, text) in enumerate(scenario.history):
            append_line(
                session.ws, key, role=role, text=text, event_id=f"seed-{index:02d}", at=SEEDED_AT
            )
        turn = session.say(scenario.number, scenario.name, scenario.message)
    return score(scenario, turn)
