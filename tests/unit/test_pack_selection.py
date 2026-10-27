"""The files a customer's pack carries: the answer to an answerable question is in it (AC24, AC23).

These use the made-up shop of the live evaluation. The conversation in the last test is the one in
which the assistant told an English customer that it accepts Pix and, two turns later, took it back
because the pack of that turn did not carry the file that says so.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from evals.attendant.cases import load_cases, load_shop
from tests.support import factories as f
from tests.support.workspace import make_workspace
from tn_assist.config import load_config, save_config
from tn_assist.events import parse_events
from tn_assist.ingest import ingest
from tn_assist.knowledge import seed_defaults, write_knowledge
from tn_assist.memory import Card, Fact, append_line
from tn_assist.pack import CORE_FILES, EXTRA_FILES, Pack, build_pack
from tn_assist.paths import ContactKey, Workspace
from tn_assist.queue import WorkItem, enqueue

NOW = datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc)
ANA = ContactKey.parse(f.ANA)


def shop_workspace(tmp_path: Path) -> Workspace:
    ws = make_workspace(tmp_path)
    shop = load_shop()
    for item in shop.files:
        write_knowledge(
            ws, item.path, item.text, visibility=item.visibility, description=item.description
        )
    seed_defaults(ws, "pt")
    save_config(ws, {**load_config(ws), "business_name": shop.business})
    return ws


def pack_for(ws: Workspace, message: str) -> Pack:
    event = f.inbound_text(preview=message)
    ingest(ws, parse_events(f.payload(event)), NOW)
    item = enqueue(ws, WorkItem.new("contact", f.ANA, (event["id"],), NOW))
    return build_pack(ws, item, now=NOW)


def test_how_can_i_pay_reaches_the_file_that_answers_it(tmp_path: Path) -> None:
    pack = pack_for(shop_workspace(tmp_path), "Como posso pagar?")
    assert "pricing.md" in pack.sources
    assert "Pix, cartão de débito e cartão de crédito" in pack.text


@pytest.mark.parametrize(
    ("message", "file", "fact"),
    [
        ("Vocês aceitam Pix?", "pricing.md", "Pix"),
        ("Posso pagar no cartão de crédito?", "pricing.md", "cartão de crédito"),
        ("Qual é o prazo para trocar ou devolver?", "policies.md", "7 dias corridos"),
        ("Vocês reembolsam se eu não gostar?", "policies.md", "7 dias corridos"),
        ("Which payment methods do you accept?", "pricing.md", "Pix"),
        ("How long do I have to return or exchange something?", "policies.md", "7 dias corridos"),
        ("How much is shipping to Centro and how long does it take?", "policies.md", "R$ 15,00"),
        ("Can I pay by card?", "pricing.md", "cartão de débito"),
    ],
)
def test_an_answerable_question_reaches_the_file_with_the_answer(
    tmp_path: Path, message: str, file: str, fact: str
) -> None:
    pack = pack_for(shop_workspace(tmp_path), message)
    assert file in pack.sources, (message, pack.sources)
    assert fact in pack.text


#: The file of the fictitious shop that holds the answer to each control of the live evaluation.
CONTROL_FILE = {
    "control-hours-saturday": "company.md",
    "control-price-aurora": "catalog.md",
    "control-delivery-centro": "policies.md",
    "control-payment": "pricing.md",
    "control-returns": "policies.md",
    "control-candle": "catalog.md",
    "control-payment-en": "pricing.md",
    "control-returns-en": "policies.md",
    "control-delivery-en": "policies.md",
    "control-transfer": "pricing.md",
    "control-gift-wrap": "faq.md",
}


def test_every_control_of_the_evaluation_gets_the_file_that_answers_it(tmp_path: Path) -> None:
    controls = [c for c in load_cases() if not c.unsupported]
    assert {c.id for c in controls} == set(CONTROL_FILE)
    for control in controls:
        pack = pack_for(shop_workspace(tmp_path / control.id), control.message)
        assert CONTROL_FILE[control.id] in pack.sources, (control.id, pack.sources)


def test_the_core_files_never_use_up_the_slots(tmp_path: Path) -> None:
    ws = shop_workspace(tmp_path)
    message = (
        "Quanto custa a luminária com frete? Posso trocar, pagar no Pix? Tem embalagem de presente?"
    )
    pack = pack_for(ws, message)
    core = [p for p in pack.sources if p in CORE_FILES]
    extras = [p for p in pack.sources if p not in CORE_FILES]
    assert core == list(CORE_FILES)
    assert len(extras) == EXTRA_FILES and len(set(extras)) == EXTRA_FILES


def test_the_files_are_the_same_every_time(tmp_path: Path) -> None:
    ws = shop_workspace(tmp_path)
    first = pack_for(ws, "Quanto é o frete e posso pagar no Pix?").sources
    for _ in range(3):
        assert pack_for(ws, "Quanto é o frete e posso pagar no Pix?").sources == first


# The conversation of the live run, as the assistant and the customer wrote it. Fictitious.
PIX_CONVERSATION = [
    ("contact", "Hi! What are your opening hours on Saturday?"),
    (
        "assistant",
        "I am this business's virtual assistant, an AI.\n\nHi Nathan! On Saturdays we're open from "
        "9am to 1pm, both at the store (Rua dos Ipês, 120, Centro, Vila Aurora) and here on "
        "WhatsApp. We're closed on Sundays.",
    ),
    ("contact", "And where is the shop?"),
    ("assistant", "The shop is at Rua dos Ipês, 120, Centro, in Vila Aurora."),
    ("contact", "Desculpa, prefiro português. Quanto custa o difusor Brisa?"),
    (
        "assistant",
        "Claro, Nathan! O Difusor de ambiente Brisa (250 ml) custa R$ 69,00. Se quiser, você pode "
        "retirar na loja sem custo ou comprar por aqui mesmo pelo WhatsApp.",
    ),
    ("contact", "Vocês aceitam Pix?"),
    (
        "assistant",
        "Boa pergunta, Nathan! Aceitamos Pix, cartão de débito e cartão de crédito.",
    ),
    ("contact", "Great. Can I return the Aurora lamp if I don't like it?"),
    (
        "assistant",
        "Yes! You can return the Aurora lamp within 7 calendar days of receiving it, as long as "
        "it's unused and in its original packaging. Also, we accept Pix, as well as debit and "
        "credit cards.",
    ),
]


def with_history(ws: Workspace, lines: list[tuple[str, str]]) -> None:
    card = Card.load(ws, ANA)
    card.merge(
        [
            Fact("name", "Nathan Rowe", "platform", "2026-10-24T13:00:00Z"),
            Fact("interest", "return the Aurora lamp", "inferred", "2026-10-24T13:50:00Z"),
        ]
    )
    card.save()
    for index, (role, text) in enumerate(lines):
        append_line(
            ws,
            ANA,
            role=role,
            text=text,
            event_id=f"history-{index:02d}",
            at="2026-10-24T13:00:00Z",
        )


def test_the_pix_the_assistant_just_accepted_is_not_taken_back_for_lack_of_its_file(
    tmp_path: Path,
) -> None:
    ws = shop_workspace(tmp_path)
    with_history(ws, PIX_CONVERSATION)
    pack = pack_for(ws, "Thanks, that's all!")
    # nothing in "thanks" names a subject, but the last answers talk about Pix and cards, and the
    # pack of the next turn must still carry the file that says the assistant was right
    assert "pricing.md" in pack.sources
    assert "Pix, cartão de débito e cartão de crédito" in pack.text


def test_the_first_time_the_pix_question_comes_the_payment_file_is_there(tmp_path: Path) -> None:
    ws = shop_workspace(tmp_path)
    with_history(ws, PIX_CONVERSATION[:4])
    assert "pricing.md" in pack_for(ws, "Vocês aceitam Pix?").sources


def test_a_greeting_gets_the_core_files_and_nothing_else(tmp_path: Path) -> None:
    assert pack_for(shop_workspace(tmp_path), "Oi, boa tarde!").sources == CORE_FILES
