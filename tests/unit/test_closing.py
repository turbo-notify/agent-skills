"""When a customer's words allow the assistant to say nothing (closing.py).

The model may ask for silence; this guard decides whether the words permit it. The tests have two
sides: what is silenced (a thank you, an ok, a thumbs up) and what never is (a question, a request,
a greeting, a yes, an offer taken up, anything with a number, every other language).
"""

from __future__ import annotations

import pytest

from tn_assist.closing import ANCHORS, FILLERS, MAX_CHARS, closes_conversation, is_closing


@pytest.mark.parametrize(
    "text",
    [
        "👍",
        "👍🏽",
        "🙏",
        "❤️",
        "😊 👍",
        "ok",
        "Ok!",
        "OK 👍",
        "okkk",
        "okay, thanks",
        "obrigado",
        "Obrigada!!",
        "muito obrigada",
        "obrigadooo 😊",
        "valeu",
        "Valeu, obrigado!",
        "vlw",
        "blz",
        "beleza, obrigado",
        "perfeito",
        "Perfeito, era só isso. Obrigada!",
        "ok, tá bom",
        "show, obrigado pela ajuda",
        "tchau!",
        "thanks",
        "Thanks!",
        "thank you",
        "Thank you so much!",
        "thanks a lot",
        "Thanks, that's all!",
        "Thanks, that’s all!",
        "got it, thanks",
        "great, thank you for your help",
        "sounds good, thanks",
        "perfect",
        "ok bye",
        "ＯＫ",  # full-width letters read as plain ones
        "ok​",  # a zero-width space is not a word
    ],
)
def test_a_thank_you_an_ok_or_a_thumbs_up_closes_the_conversation(text: str) -> None:
    assert is_closing(text), text


@pytest.mark.parametrize(
    "text",
    [
        # a question, with or without the mark
        "ok, e o frete?",
        "ok, e o frete",
        "obrigado, quanto custa?",
        "obrigado quanto custa",
        "thanks, what time do you open",
        "thanks, what time do you open?",
        "ok?",
        "ok ¿",
        "ok？",
        "ok؟",
        "👍❓",
        "👍?",
        "?",
        # a request, a yes, an offer taken up, a greeting: they need an answer
        "sim",
        "yes",
        "pode",
        "quero",
        "please",
        "oi",
        "bom dia",
        "boa tarde, obrigado",
        "hello",
        "good morning",
        "tudo bem",
        # content next to a thanks
        "obrigado, vou passar aí no sábado",
        "Awesome, thanks! I'll come by on Saturday.",
        "ok, mas e o desconto",
        "ok but I need a discount",
        "thanks for the discount",
        # a number is content
        "ok 15",
        "ok 2x",
        "thanks 5",
        "1️⃣",
        # nothing to read
        "",
        "   ",
        "...",
        "!!!",
        "—",
        # other languages are never silenced
        "gracias",
        "merci",
        "danke",
        # too long, however polite
        "ok " * 21,
        "obrigado " * 8,
        "👍" * (MAX_CHARS + 1),
        "ok ok ok ok ok ok ok",
    ],
)
def test_anything_else_is_not_a_closing(text: str) -> None:
    assert not is_closing(text), text


def test_every_message_the_customer_has_not_been_answered_must_close_it() -> None:
    assert closes_conversation(["ok", "obrigado", "👍"])
    assert not closes_conversation(["quanto custa o frete", "ok"])
    assert not closes_conversation(["ok", "e o frete?"])
    assert not closes_conversation([])  # no message, nothing to be silent about
    assert not closes_conversation(["ok", ""])  # a message with no text (a photo) needs a look


def test_the_word_lists_hold_no_question_word_and_no_greeting() -> None:
    """The guarantee that a question never passes rests on what is NOT in the lists."""
    never = set(
        """
        qual quais quanto quanta quantos quantas quando onde como quem porque que tem tenho temos
        pode posso podem quero queria gostaria preciso sim nao oi ola dia noite manha
        what which who whom whose when where why how do does can could would should will want need
        please yes no hi hello hey morning afternoon evening
        preco valor frete entrega desconto horario abre fecha custa
        price cost shipping delivery discount hours open close
        """.split()
    )
    allowed = ANCHORS | FILLERS
    assert not never & allowed, sorted(never & allowed)


def test_an_anchor_is_needed_so_small_words_alone_do_not_close_anything() -> None:
    for text in ("a", "o", "e", "you", "isso", "all", "muito", "the"):
        assert not is_closing(text), text


def test_two_words_can_close_together() -> None:
    assert is_closing("era isso")
    assert is_closing("that's all")
    assert is_closing("all good")
    assert not is_closing("isso")
    assert not is_closing("all")


def test_repeated_letters_are_read_as_the_word() -> None:
    for text in ("obrigadooo", "valeuuu", "blzzz", "thankssss", "coool", "okkkkk"):
        assert is_closing(text), text
