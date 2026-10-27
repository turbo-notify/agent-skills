"""The evaluation's language detector (AC23): a word count, the same answer on every run."""

from __future__ import annotations

import pytest

from evals.attendant.language import EN, PT, UNKNOWN, detect


@pytest.mark.parametrize(
    "text",
    [
        "Olá! A loja abre de segunda a sexta, das 9h às 18h.",
        "Vou pedir para uma pessoa da equipe continuar essa conversa com você.",
        "Sim, aceitamos Pix e cartão. Qual é o seu bairro?",
        "Não tenho essa informação aqui.",
        "Obrigado pelo contato!",
        "Claro! O frete é de R$ 15,00.",
    ],
)
def test_portuguese_is_recognised(text: str) -> None:
    assert detect(text).language == PT


@pytest.mark.parametrize(
    "text",
    [
        "Hello! The shop is open on Saturday from 9 am to 1 pm.",
        "I will ask someone on the team to continue this conversation with you.",
        "Yes, we take Pix and credit cards. Where do you live?",
        "I do not have that information.",
        "Thanks for getting in touch!",
        "Of course! Shipping is R$ 15.00.",
    ],
)
def test_english_is_recognised(text: str) -> None:
    assert detect(text).language == EN


def test_names_and_products_in_the_other_language_do_not_tip_the_balance() -> None:
    assert (
        detect("Claro! O Pendente Lunar custa R$ 239,00 e a Casa Lumiar aceita Pix.").language == PT
    )
    assert detect("Sure! The Pendente Lunar is R$ 239.00 and Casa Lumiar takes Pix.").language == EN


@pytest.mark.parametrize("text", ["", "👍", "Pix", "Casa Lumiar"])
def test_a_text_without_clear_words_is_unknown(text: str) -> None:
    assert detect(text).language == UNKNOWN
