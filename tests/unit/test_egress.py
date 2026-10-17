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
