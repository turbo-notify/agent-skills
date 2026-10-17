from __future__ import annotations

from pathlib import Path

from tests.support import factories as f
from tests.support.knowledge import seed_knowledge
from tests.support.workspace import make_workspace
from tn_assist.egress import check_egress
from tn_assist.memory import Card, Fact
from tn_assist.paths import ContactKey, Workspace
from tn_assist.sources import SourceClass, SourceRegistry

ANA = ContactKey.parse(f.ANA)
BRUNO = ContactKey.parse(f.BRUNO)


def setup(tmp_path: Path) -> Workspace:
    ws = make_workspace(tmp_path)
    seed_knowledge(ws)
    for key, name in ((ANA, "Ana Souza"), (BRUNO, "Bruno Lima")):
        card = Card.load(ws, key)
        card.merge([Fact("name", name, "admin", "2026-10-17T10:00:00Z")])
        card.save()
    return ws


def verdict(ws: Workspace, text: str, contact: ContactKey = ANA):  # type: ignore[no-untyped-def]
    return check_egress(ws, text, contact=contact)


def test_plain_answer_from_public_knowledge_passes(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    assert verdict(ws, "O plano anual custa R$ 490 e a entrega leva até 5 dias úteis.").ok


def test_other_contacts_number_is_blocked_in_every_spelling(tmp_path: Path) -> None:  # AC48
    ws = setup(tmp_path)
    for spelling in (f.BRUNO, "+55 (11) 96666-5555", "55 11 96666 5555", "11966665555"):
        result = verdict(ws, f"Fale com ele: {spelling}")
        assert not result.ok and "other_contact" in result.reasons, spelling


def test_other_contacts_name_is_blocked_but_own_name_is_fine(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    assert not verdict(ws, "O Bruno Lima também perguntou isso.").ok
    assert verdict(ws, "Claro, Ana Souza! O plano anual custa R$ 490.").ok


def test_admin_numbers_are_blocked(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    assert "other_contact" in verdict(ws, f"Ligue para {f.OWNER}").reasons


def test_own_number_is_allowed(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    assert verdict(ws, f"Anotei o seu número, {f.ANA}.").ok


def test_unknown_phone_number_is_blocked(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    assert "personal_data" in verdict(ws, "Chame no 11 91234-5678").reasons


def test_phone_in_public_knowledge_is_allowed(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    ws.path("knowledge", "company.md").write_text("Telefone da loja: 11 4002-8922.\n")
    assert verdict(ws, "Ligue para 11 4002-8922 em horário comercial.").ok


def test_internal_knowledge_text_is_blocked(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    result = verdict(ws, "A margem de 62% no plano anual é boa.")
    assert not result.ok and "non_public" in result.reasons


def test_registered_canary_from_a_non_public_source_is_blocked(tmp_path: Path) -> None:  # AC46/AC48
    ws = setup(tmp_path)
    reg = SourceRegistry.load(ws)
    reg.discovered(["crm"])
    reg.classify("crm", SourceClass.INTERNAL, confirmed=True)
    reg.add_marker("crm", "CRM-CANARY-9")
    assert "non_public" in verdict(ws, "olhei no sistema: crm-canary-9").reasons


def test_personal_data_patterns_are_blocked(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    assert not verdict(ws, "CPF 123.456.789-09 confirmado").ok
    assert not verdict(ws, "cartão 4111 1111 1111 1111").ok
    assert not verdict(ws, "mande para joao.silva@example.com").ok


def test_email_in_public_knowledge_or_own_data_is_allowed(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    ws.path("knowledge", "company.md").write_text("Contato: vendas@loja.example\n")
    assert verdict(ws, "Escreva para vendas@loja.example").ok
    card = Card.load(ws, ANA)
    card.merge([Fact("email", "ana@example.com", "admin", "2026-10-17T10:00:00Z")])
    card.save()
    assert verdict(ws, "Vou enviar para ana@example.com").ok


def test_credential_shaped_strings_are_blocked(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    for text in (
        "minha chave é tn_live_abcdefgh12345678",
        "tn_oat_zzzzzzzz00000000",
        "tn_oup_aaaaaaaa11111111",
    ):
        assert "credential" in verdict(ws, text).reasons


def test_prices_and_dates_are_not_mistaken_for_phones(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    assert verdict(ws, "Pedido em 17/10/2026, total R$ 1.490,00 em 12x.").ok


def test_unicode_tricks_do_not_hide_another_contacts_number(
    tmp_path: Path,
) -> None:  # security review
    ws = setup(tmp_path)
    tricks = [
        "+55 11 96666\u20115555",  # non-breaking hyphen
        "55\u00a011\u00a096666\u00a05555",  # non-breaking spaces
        "\uff15\uff15\uff11\uff11\uff19\uff16\uff16\uff16\uff16\uff15\uff15\uff15\uff15",  # wide
        "55119666\u200b65555",  # zero width space inside
        "55/11/96666/5555",
        "55_11_96666_5555",
        "55\u2013 11 \u2014 96666 5555",
        "5 5 1 1 9 6 6 6 6 5 5 5 5",
    ]
    for trick in tricks:
        assert not verdict(ws, f"Fale com {trick}").ok, trick


def test_credential_shapes_are_found_inside_words_and_through_invisible_characters(
    tmp_path: Path,
) -> None:
    ws = setup(tmp_path)
    for text in (
        "xtn_live_abcdefgh12345678",
        "tn_\u200blive_abcdefgh12345678",
        "A=tn_oat_zzzzzzzz00000000",
    ):
        assert "credential" in verdict(ws, text).reasons, text


def test_inferred_facts_do_not_whitelist_a_leak(tmp_path: Path) -> None:  # security review
    ws = setup(tmp_path)
    card = Card.load(ws, ANA)
    card.merge([Fact("note", f.BRUNO, "inferred", "2026-10-17T10:00:00Z")])
    card.save()
    assert "other_contact" in verdict(ws, f"O numero e {f.BRUNO}").reasons
    card.merge([Fact("note", "ana@example.com", "inferred", "2026-10-17T10:01:00Z")])
    card.save()
    assert not verdict(ws, "escreva para ana@example.com").ok


def test_what_the_customer_said_is_allowed_back(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    from tn_assist.memory import append_line

    append_line(
        ws,
        ANA,
        role="contact",
        text="meu email e ana@example.com",
        event_id="evt_1",
        at="2026-10-17T10:00:00Z",
    )
    assert verdict(ws, "Confirmo o envio para ana@example.com").ok


def test_a_phone_number_dressed_as_money_or_a_date_is_still_found(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    for text in (
        "so R$ 55.119.666.655,55 hoje",
        "no dia 55/11/96666-5555",
        "US$ 5511966665555",
    ):
        assert "other_contact" in verdict(ws, text).reasons, text


def test_other_unicode_digits_do_not_hide_a_number(tmp_path: Path) -> None:  # security review
    ws = setup(tmp_path)
    arabic = "".join(chr(0x0660 + int(d)) for d in f.BRUNO)  # Arabic-Indic digits
    devanagari = "".join(chr(0x0966 + int(d)) for d in f.BRUNO)
    for text in (f"Ligue {arabic}", f"Ligue {devanagari}"):
        assert "other_contact" in verdict(ws, text).reasons, text


def test_long_numbers_cannot_hide_inside_money_or_a_date(tmp_path: Path) -> None:  # security review
    ws = setup(tmp_path)
    for text in (
        "CPF R$ 12345678909",  # 11 digits after a currency symbol
        "US$ 4111 1111 1111 1111",
        "so 31/12/12345678909 hoje",
    ):
        assert not verdict(ws, text).ok, text
    assert verdict(ws, "Total R$ 1.234,56 em 31/12/2026").ok


def test_a_one_word_name_a_stranger_made_the_model_infer_cannot_block_other_replies(
    tmp_path: Path,
) -> None:  # security review
    ws = setup(tmp_path)
    stranger = ContactKey.parse("5511933334444")
    card = Card.load(ws, stranger)
    card.merge([Fact("name", "Obrigado", "inferred", "2026-10-17T10:01:00Z")])
    card.save()
    assert verdict(ws, "Obrigado pelo contato! O plano anual custa R$ 490.").ok
    card.merge([Fact("name", "Carlos Mendes", "inferred", "2026-10-17T10:02:00Z")])
    card.save()
    assert not verdict(ws, "Carlos Mendes também perguntou isso.").ok  # a full name still counts


def test_what_the_team_wrote_about_a_customer_does_not_come_back_as_a_quote(
    tmp_path: Path,
) -> None:  # security review
    ws = setup(tmp_path)
    card = Card.load(ws, ANA)
    card.merge(
        [
            Fact(
                "note_001",
                "cliente dificil, sempre pede desconto no ultimo dia",
                "attendant",
                "2026-10-17T10:03:00Z",
            )
        ]
    )
    card.save()
    assert "non_public" in verdict(ws, "Sei que voce sempre pede desconto no ultimo dia.").reasons
    assert verdict(ws, "O plano anual custa R$ 490 e a entrega leva até 5 dias úteis.").ok


def test_a_short_team_note_is_still_caught_whole(tmp_path: Path) -> None:  # security review
    ws = setup(tmp_path)
    card = Card.load(ws, ANA)
    card.merge([Fact("note_002", "ja pagou o sinal", "attendant", "2026-10-17T10:04:00Z")])
    card.save()
    assert "non_public" in verdict(ws, "Vi aqui que voce ja pagou o sinal, certo?").reasons
    assert verdict(ws, "O plano anual custa R$ 490.").ok


def test_a_list_of_national_format_numbers_is_blocked(tmp_path: Path) -> None:  # review
    ws = setup(tmp_path)
    text = "Contatos: 11 99999-0001, 21 98888-0002, 11 96666-5555"  # the last one is Bruno's
    assert "other_contact" in verdict(ws, text).reasons
    assert "other_contact" in verdict(ws, "Fale com 11 6666-5555 ou 31 97777-0003").reasons  # no 9
    assert verdict(ws, "Pedido 123456, total R$ 490, entrega em 5 dias uteis.").ok


def test_a_one_word_whatsapp_name_is_still_protected_but_a_model_fact_cannot_replace_it(
    tmp_path: Path,
) -> None:  # security review
    ws = setup(tmp_path)
    other = ContactKey.parse("5511933334444")
    card = Card.load(ws, other)
    card.merge([Fact("name", "Marcelo", "platform", "2026-10-17T10:00:00Z")])  # the push name
    card.merge([Fact("name", "Obrigado", "inferred", "2026-10-17T10:05:00Z")])  # a forged inference
    card.save()
    assert Card.load(ws, other).get("name").value == "Marcelo"  # type: ignore[union-attr]
    assert not verdict(ws, "O Marcelo também perguntou isso.").ok
    assert verdict(ws, "Obrigado pelo contato! O plano anual custa R$ 490.").ok


def test_a_display_name_that_is_a_word_every_reply_uses_does_not_block_other_customers(
    tmp_path: Path,
) -> None:  # security review
    ws = setup(tmp_path)
    other = ContactKey.parse("5511933334444")
    card = Card.load(ws, other)
    card.merge([Fact("name", "Obrigado", "platform", "2026-10-17T10:00:00Z")])
    card.save()
    assert verdict(ws, "Obrigado pelo contato! O plano anual custa R$ 490.").ok
    card = Card.load(ws, other)
    card.merge([Fact("name", "Obrigado", "admin", "2026-10-17T10:05:00Z")])  # a person confirmed it
    card.save()
    assert not verdict(ws, "Obrigado pelo contato!").ok
