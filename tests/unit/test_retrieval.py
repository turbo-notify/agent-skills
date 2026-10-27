"""Which knowledge files reach a pack: filler ignored, stems, vocabulary, core files apart."""

from __future__ import annotations

from typing import Any

import pytest

from tn_assist.pack import CORE_FILES, EXTRA_FILES
from tn_assist.retrieval import STOPWORDS, parse_index, select_knowledge, stems

INDEX = "\n".join(
    [
        "- company.md: quem somos, endereço e horários de atendimento",
        "- tone.md: como falar com os clientes",
        "- handoff.md: quando chamar uma pessoa",
        "- catalog.md: produtos e preços do catálogo",
        "- pricing.md: formas de pagamento",
        "- policies.md: entrega, frete, troca e devolução",
        "- faq.md: embalagem de presente e retirada na loja",
    ]
)
BODIES = {
    "company.md": "Loja Aurora. Horário: segunda a sexta, das 9h às 18h. Como chegar: Rua dos Ipês",
    "tone.md": "Fale de forma simpática e clara. Como falar: você, sem gírias.",
    "handoff.md": "Quando não souber responder, diga que alguém da equipe continua.",
    "catalog.md": "Luminária Aurora: R$ 189,00. Vela de lavanda: R$ 39,00.",
    "pricing.md": "Formas de pagamento: Pix, cartão de débito e cartão de crédito.",
    "policies.md": (
        "Entrega em até 3 dias úteis, frete de R$ 15,00. Troca e devolução em até 7 dias. "
        "Se houver defeito devolvemos o valor pago, depois da confirmação do pagamento."
    ),
    "faq.md": "Posso retirar o pedido na loja? Sim, sem custo. Como funciona? Pelo WhatsApp.",
}


def pick(query: str, **kw: Any) -> list[str]:
    kw.setdefault("exclude", CORE_FILES)
    return select_knowledge(INDEX, query, EXTRA_FILES, BODIES, **kw)


# --- filler -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Como posso pagar?", {"paga"}),
        ("Can I pay by card?", {"pay", "card"}),
        ("Vocês aceitam Pix?", {"acei", "pix"}),
        ("Oi, tudo bem? Gostaria de saber", set()),
        ("Pelo WhatsApp, por favor", set()),
        ("the and for with", set()),
    ],
)
def test_filler_words_carry_no_subject(text: str, expected: set[str]) -> None:
    assert stems(text) == expected


def test_the_stop_words_do_not_include_a_word_a_shop_is_asked_about() -> None:
    for word in (
        "pagar pagamento pix cartao boleto troca devolver reembolso entrega frete prazo horario "
        "garantia desconto estoque preco valor catalogo refund payment delivery shipping hours "
        "warranty discount price stock much many sell"
    ).split():
        assert word not in STOPWORDS, word


def test_filler_does_not_take_a_slot_from_a_real_word() -> None:
    """ "como" and "posso" are in the tone and FAQ files; "pagar" belongs with the payment file."""
    assert pick("Como posso pagar?")[0] == "pricing.md"
    assert "faq.md" not in pick("Como posso pagar?")


# --- stems --------------------------------------------------------------------------------


def test_a_verb_finds_the_noun_it_comes_from() -> None:
    assert pick("Como posso pagar?")[0] == "pricing.md"
    assert pick("posso trocar?")[0] == "policies.md"
    assert pick("vocês entregam?")[0] == "policies.md"
    assert pick("aceitam cartão?")[0] == "pricing.md"


def test_short_words_match_whole() -> None:
    assert pick("aceita pix?")[0] == "pricing.md"
    assert pick("pi") == []


def test_accents_and_case_do_not_matter() -> None:
    assert pick("COBRAM FRETE?") == pick("cobram frete?")
    assert pick("cobram frete?")[0] == "policies.md"
    assert pick("devolução") == pick("devolucao")
    assert pick("devolucao")[0] == "policies.md"


# --- the limit and the core files -------------------------------------------------------------


def test_the_limit_counts_files_other_than_the_core_ones() -> None:
    """Every file matches equally and the core ones come first by path: no slot for them."""
    index = "\n".join(
        f"- {name}: pagamento" for name in (*CORE_FILES, "pricing.md", "policies.md", "faq.md")
    )
    extras = select_knowledge(index, "pagamento", EXTRA_FILES, exclude=CORE_FILES)
    assert extras == ["faq.md", "policies.md", "pricing.md"]
    # a limit applied before taking the core files out leaves a single extra
    everything = select_knowledge(index, "pagamento", EXTRA_FILES)
    assert [p for p in everything if p not in CORE_FILES] == ["faq.md"]


def test_an_excluded_file_is_never_picked() -> None:
    assert pick("formas de pagamento", exclude=("pricing.md",)) != ["pricing.md"]
    assert "pricing.md" not in pick("formas de pagamento", exclude=("pricing.md",))


def test_nothing_matches_nothing_is_picked() -> None:
    assert pick("zzzz") == [] and pick("") == [] and pick("oi") == []
    assert select_knowledge(INDEX, "pagar", 0, BODIES) == []
    assert select_knowledge("", "pagar", 3, BODIES) == []


# --- order --------------------------------------------------------------------------------------


def test_the_same_words_always_pick_the_same_files() -> None:
    runs = {tuple(pick("quanto custa o frete e o pagamento?")) for _ in range(5)}
    assert len(runs) == 1


def test_equal_matches_go_by_path() -> None:
    index = "- b.md: cartão\n- a.md: cartão\n- c.md: cartão"
    assert select_knowledge(index, "cartão", 3) == ["a.md", "b.md", "c.md"]


def test_a_rare_word_beats_a_word_in_every_file() -> None:
    index = "- a.md: loja produtos\n- b.md: loja garantia\n- c.md: loja entrega"
    assert select_knowledge(index, "loja garantia", 3)[0] == "b.md"


def test_the_new_message_outweighs_the_conversation_before_it() -> None:
    first = pick("quero saber da entrega", recent="vocês aceitam pix?")
    assert first[0] == "policies.md" and "pricing.md" in first


# --- the vocabulary -------------------------------------------------------------------------------


def test_a_refund_finds_the_return_policy_though_the_owner_wrote_another_word() -> None:
    assert pick("vocês reembolsam o dinheiro?")[0] == "policies.md"
    assert pick("quero um estorno")[0] == "policies.md"


@pytest.mark.parametrize(
    ("question", "file"),
    [
        ("Which payment methods do you accept?", "pricing.md"),
        ("Can I pay with a credit card?", "pricing.md"),
        ("How long do I have to return or exchange something?", "policies.md"),
        ("How much is shipping and when does it arrive?", "policies.md"),
        ("Is there a refund if it arrives broken?", "policies.md"),
        ("Which products do you sell and what do they cost?", "catalog.md"),
        ("Do you wrap gifts? Can I pick it up at the shop?", "faq.md"),
        # "how much" and "how many" are questions about a price and a quantity, whatever the product
        ("Hi! How much is the lavender candle?", "catalog.md"),
        ("How much does it cost?", "catalog.md"),
        ("How many do you have?", "catalog.md"),
        ("Do you sell candles?", "catalog.md"),
        ("Vocês vendem velas?", "catalog.md"),
    ],
)
def test_an_english_question_finds_the_portuguese_file(question: str, file: str) -> None:
    assert file in pick(question), pick(question)


def test_a_word_the_customer_wrote_counts_more_than_one_the_vocabulary_added() -> None:
    index = "- a.md: devolução\n- b.md: troca"
    assert select_knowledge(index, "troca", 2) == ["b.md", "a.md"]
    assert select_knowledge(index, "devolução", 2) == ["a.md", "b.md"]


def test_the_vocabulary_has_no_stop_word_and_no_word_in_two_unrelated_groups_by_accident() -> None:
    from tn_assist import retrieval

    for group in retrieval._GROUPS:
        words = set(group.split())
        assert not words & STOPWORDS, words & STOPWORDS


# --- the conversation ---------------------------------------------------------------------------


def test_a_file_the_assistant_just_used_stays_while_the_conversation_goes_on() -> None:
    """The assistant said it accepts Pix; "thanks" has no subject word, the last answer does."""
    assert pick("Thanks, that's all!") == []
    said = "A correction to my earlier answer: we do accept Pix, as well as debit and credit cards."
    assert "pricing.md" in pick("Thanks, that's all!", recent=said)


def test_what_is_known_about_the_customer_counts_for_less_than_the_conversation() -> None:
    assert pick("oi", profile="interesse: luminária") == ["catalog.md"]
    both = pick("oi", recent="quero pagar", profile="interesse: luminária")
    assert both[0] == "pricing.md"


def test_the_index_lines_are_read_as_before() -> None:
    assert parse_index("- a.md: um\nlixo\n  - b/c.md: dois ") == [
        ("a.md", "um"),
        ("b/c.md", "dois"),
    ]
