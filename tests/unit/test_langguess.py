"""A guess at a customer's language, used only when the helper must write the hand-off itself."""

from __future__ import annotations

import pytest

from tn_assist.langguess import _EN, _PT, guess_language


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hi! What time do you open on Saturday?", "en"),
        ("Which payment methods do you accept?", "en"),
        ("Is there a discount code I can use? I saw a post saying 15% off.", "en"),
        ("Thanks, that's all!", "en"),
        ("Oi! Vocês abrem no sábado?", "pt"),
        ("Boa tarde, qual o prazo de troca?", "pt"),
        ("Quanto custa a luminária Aurora? Vocês entregam no Centro?", "pt"),
        ("VOCÊS ENTREGAM?", "pt"),
        ("voces entregam", "pt"),
        ("Bom dia! Vi o Pendente Lunar e achei lindo. Quanto custa? Thanks!", "pt"),
        ("Desculpa, prefiro português. Quanto custa o difusor Brisa?", "pt"),
    ],
)
def test_the_language_with_more_of_its_own_common_words_wins(text: str, expected: str) -> None:
    assert guess_language(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "👍", "ok", "Aurora", "Lunar 40", "+55 11 97777-0001", "Thanks, obrigada", "123 456"],
)
def test_no_clear_winner_is_no_guess(text: str) -> None:
    assert guess_language(text) is None


def test_the_word_lists_share_no_word() -> None:
    """A word both languages use would count for both and prove nothing."""
    assert not _PT & _EN, sorted(_PT & _EN)


def test_a_customer_is_not_misread_by_the_name_of_a_product() -> None:
    assert guess_language("Is the Brisa lamp in stock?") == "en"
    assert guess_language("A Brisa está em estoque?") == "pt"
