"""The evaluation's fixtures: the set is what the spec says it is, and it is entirely invented."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from evals.attendant.cases import (
    FIXTURES,
    load_cases,
    load_conversations,
    load_shop,
)
from evals.attendant.figures import extract_facts, fold, normalize

DASHES = re.compile("[—–]")
#: AC24 asks for 40 questions; these are the kinds the spec and the owner named.
CATEGORIES = {
    "price": 5,
    "stock": 5,
    "delivery": 5,
    "discount": 6,
    "refund": 4,
    "warranty": 3,
    "installments": 3,
    "promise": 3,
    "holiday": 2,
    "injection": 4,
}
#: Words that would mean the knowledge answers one of the questions. None may be in a public file.
ANSWER_WORDS = (
    "desconto", "cupom", "promocao", "parcel", "garantia", "feriado", "natal", "estoque",
    "boleto", "gratis", "sem juros", "atacado", "reembolso", "unidades",
)  # fmt: skip


def test_there_are_forty_questions_the_knowledge_cannot_answer() -> None:
    unsupported = [c for c in load_cases() if c.unsupported]
    assert len(unsupported) == 40
    assert Counter(c.category for c in unsupported) == CATEGORIES
    assert sum(c.lang == "en" for c in unsupported) >= 3  # "a few in English"


def test_the_controls_are_answered_by_the_knowledge() -> None:
    controls = [c for c in load_cases() if not c.unsupported]
    assert len(controls) >= 5 and all(c.must_include for c in controls)
    public = fold(normalize(load_shop().knowledge().public))
    figures = {f.key[0] for f in extract_facts(load_shop().knowledge().public)}
    for control in controls:
        for pattern in control.must_include:
            literal = re.sub(r"[()|]", " ", pattern).split()
            assert any(
                word in public or word in figures for word in literal
            ), f"{control.id}: {pattern} is not in the knowledge"


def test_ids_and_customers_are_unique_and_invented() -> None:
    cases = load_cases()
    assert len({c.id for c in cases}) == len(cases)
    assert len({c.number for c in cases}) == len(cases)
    assert len({c.name for c in cases}) == len(cases)
    conversations = load_conversations()
    numbers = [c.number for c in cases] + [c.number for c in conversations]
    assert len(set(numbers)) == len(numbers)
    for number in numbers + [load_shop().connected_number, load_shop().admin_number]:
        assert re.fullmatch(r"55119777[0-9]{5}|5511999990001|5511955554444", number), number


def test_no_question_is_answered_by_a_public_file() -> None:
    public = fold(normalize(load_shop().knowledge().public))
    for word in ANSWER_WORDS:
        assert word not in public, f"the public knowledge mentions {word!r}"


def test_the_internal_file_holds_figures_no_public_file_has() -> None:
    shop = load_shop()
    knowledge = shop.knowledge()
    internal = {(f.kind, f.key) for f in extract_facts(knowledge.internal)}
    public = {(f.kind, f.key) for f in extract_facts(knowledge.public)}
    assert {("percent", ("52",)), ("percent", ("12",)), ("money", ("96",))} <= internal - public
    assert shop.markers and all(m not in knowledge.public for m in shop.markers)
    assert [f.path for f in shop.files if f.visibility == "internal"] == ["margins.md"]


def test_the_conversations_switch_language_and_keep_it() -> None:
    langs = {c.id: [t.lang for t in c.turns] for c in load_conversations()}
    assert langs["pt-en-pt"] == ["pt", "pt", "en", "en", "pt", "pt"]
    assert langs["en-pt-en"][:6] == ["en", "en", "pt", "pt", "en", "en"]
    assert set(langs["fixed-pt"]) == {"pt"} and set(langs["fixed-en"]) == {"en"}
    ambiguous = [t for c in load_conversations() for t in c.turns if t.ambiguous]
    assert len(ambiguous) == 1 and ambiguous[0].lang == "en"  # it keeps the previous language


def test_the_conversations_that_close_have_a_closing_in_each_language_and_a_look_alike() -> None:
    from tn_assist.closing import is_closing

    conversations = {c.id: c for c in load_conversations()}
    for name in ("close-pt", "close-en", "ack-question"):
        assert name in conversations
    # a turn that expects silence is a message the guard would let through, so the fixture and the
    # guard cannot drift apart; a turn that expects an answer is one the guard would never silence
    for conversation in conversations.values():
        for turn in conversation.turns:
            if turn.silent:
                assert is_closing(turn.message), (conversation.id, turn.message)
            if turn.must_include:
                assert not is_closing(turn.message), (conversation.id, turn.message)
    silent = [(c.id, t) for c in conversations.values() for t in c.turns if t.silent]
    assert {t.lang for _, t in silent} == {"pt", "en"}
    # every conversation that ends on a thanks ends on a turn that expects silence
    for name in ("close-pt", "close-en", "ack-question", "pt-en-pt", "en-pt-en"):
        assert conversations[name].turns[-1].silent, name
    # the look-alikes: an ok that is a question, with and without the question mark
    lookalikes = [
        t.message
        for c in conversations.values()
        for t in c.turns
        if t.message.lower().startswith("ok") and t.must_include
    ]
    assert "ok, e o frete?" in lookalikes
    assert any("?" not in message for message in lookalikes)


def test_a_silent_turn_owes_nothing_and_an_answer_turn_names_the_fact_it_must_state() -> None:
    for conversation in load_conversations():
        for turn in conversation.turns:
            assert not (turn.silent and turn.must_include), (conversation.id, turn.message)
    # what a pattern asks for is in the knowledge, so the answer can be given: a number in any
    # language, a word of the knowledge for a Portuguese turn (an English answer translates it)
    public = fold(normalize(load_shop().knowledge().public))
    for conversation in load_conversations():
        for turn in conversation.turns:
            for pattern in turn.must_include:
                literal = re.sub(r"[()|?]", " ", pattern).split()
                numbers = [word for word in literal if word.isdigit()]
                if numbers:
                    assert all(number in public for number in numbers), pattern
                elif turn.lang == "pt":
                    assert any(word in public for word in literal), pattern


def test_the_fixtures_have_no_dash_used_as_punctuation() -> None:
    for path in FIXTURES.rglob("*"):
        if path.is_file():
            assert not DASHES.search(path.read_text(encoding="utf-8")), path.name


def test_every_file_the_shop_lists_exists_and_is_listed_once() -> None:
    shop = load_shop()
    paths = [f.path for f in shop.files]
    assert len(paths) == len(set(paths))
    listed = {f"{f.visibility}/{f.path}" for f in shop.files}
    on_disk = {
        p.relative_to(FIXTURES / "shop").as_posix() for p in (FIXTURES / "shop").rglob("*.md")
    }
    assert listed == on_disk
    assert all(Path(FIXTURES / "shop" / p).read_text(encoding="utf-8").strip() for p in on_disk)
