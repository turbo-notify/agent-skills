from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.support import factories as f
from tests.support.knowledge import seed_knowledge
from tests.support.workspace import make_workspace
from tn_assist.admins import Registry
from tn_assist.apply import (
    Draft,
    Reply,
    apply_contact_decision,
    list_drafts,
    parse_decision,
)
from tn_assist.commands import parse_command
from tn_assist.config import load_config, save_config
from tn_assist.console import capture_text, run_command
from tn_assist.events import parse_events
from tn_assist.ingest import ingest
from tn_assist.memory import Card, Fact
from tn_assist.paths import ContactKey, Workspace
from tn_assist.queue import WorkItem, enqueue
from tn_assist.state import ContactState, load_state
from tn_assist.timeutil import utc_iso

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ANA, BRUNO = ContactKey.parse(f.ANA), ContactKey.parse(f.BRUNO)
OWNER = f.OWNER


def setup(tmp_path: Path, *, lang: str = "pt") -> Workspace:
    ws = make_workspace(tmp_path)
    seed_knowledge(ws)
    cfg = load_config(ws)
    cfg.update({"lang": lang, "business_name": "Loja Aurora"})
    save_config(ws, cfg)
    return ws


def run(ws: Workspace, text: str, role: str = "admin", actor: str = OWNER, at: datetime = NOW):  # type: ignore[no-untyped-def]
    cmd = parse_command(text)
    assert cmd is not None, text
    return run_command(ws, role, actor, cmd, at)  # type: ignore[arg-type]


def customer(
    ws: Workspace,
    number: str = f.ANA,
    text: str = "oi, quero o plano anual",
    name: str = "Ana Souza",
):  # type: ignore[no-untyped-def]
    e = f.inbound_text(sender=number, sender_name=name, preview=text)
    ingest(ws, parse_events(f.payload(e)), NOW)
    return e


def escalate(
    ws: Workspace, number: str = f.ANA, reason: str = "pede desconto", at: datetime = NOW
) -> None:
    e = customer(ws, number, text=f"quero desconto {number[-4:]}")
    item = enqueue(ws, WorkItem.new("contact", number, (e["id"],), at))
    d = parse_decision(
        "contact",
        json.dumps(
            {"reply": None, "escalate": {"reason": reason, "summary": "s", "urgency": "low"}}
        ),
    )
    apply_contact_decision(ws, item, d, at, mode="auto")


def test_help_lists_only_what_the_role_can_do_in_the_users_language(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    admin = run(ws, "/ajuda").text
    assert "/ensinar" in admin and "/fila" in admin and "/esquecer" in admin
    attendant = run(ws, "/ajuda", role="attendant", actor="self").text
    assert "/fila" in attendant and "/retomar" in attendant
    assert "/ensinar" not in attendant and "/esquecer" not in attendant and "/modo" not in attendant
    ws_en = setup(tmp_path / "en", lang="en")
    assert "/queue" in run(ws_en, "/help").text


def test_attendant_gets_a_refusal_naming_what_they_can_do(tmp_path: Path) -> None:  # AC40
    ws = setup(tmp_path)
    for text in (
        "/esquecer 5511977776666",
        "/admin add 5511900000000",
        "/ensinar x",
        "/modo auto",
        "/idioma en",
    ):
        result = run(ws, text, role="attendant", actor="self")
        assert "/fila" in result.text and result.actions == []
    assert Registry.load(ws).numbers() == frozenset({OWNER})
    assert load_config(ws)["mode"] == "assist" and load_config(ws)["lang"] == "pt"


def test_status_shows_backlog_mode_and_the_connected_number_line(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    customer(ws)
    text = run(ws, "/status").text
    assert "assistido" in text.lower() and "1" in text
    assert "também é administrador" not in text
    Registry.load(ws).add(
        f.CONNECTED, None, by="configurator", via="configurator", confirmed_connected=True, now=NOW
    )
    assert "também é administrador" in run(ws, "/status").text


def test_queue_lists_escalations_paused_contacts_and_drafts(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    escalate(ws)
    out = run(ws, "/fila").text
    assert "1." in out and "Ana Souza" in out or f.ANA in out
    assert "pede desconto" in out


def test_resume_by_position_from_the_last_queue_and_by_number(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    escalate(ws, f.ANA, at=NOW)
    escalate(ws, f.BRUNO, at=NOW + timedelta(minutes=1))
    run(ws, "/fila", role="attendant", actor="self")
    result = run(ws, "/retomar 1", role="attendant", actor="self")
    assert load_state(ws, ANA).state is ContactState.ACTIVE
    assert "Ana" in result.text or f.ANA in result.text
    run(ws, f"/retomar {f.BRUNO}")
    assert load_state(ws, BRUNO).state is ContactState.ACTIVE


def test_resume_without_a_fresh_queue_asks_for_it(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    escalate(ws)
    assert "/fila" in run(ws, "/retomar 1").text
    assert load_state(ws, ANA).state is ContactState.HANDOFF


def test_resume_all_clears_the_global_pause(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    run(ws, "/pausar")
    assert load_config(ws)["paused"] is True
    run(ws, "/retomar todos")
    assert load_config(ws).get("paused") is False


def test_global_pause_stops_replies(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    run(ws, "/pausar")
    e = customer(ws)
    item = enqueue(ws, WorkItem.new("contact", f.ANA, (e["id"],), NOW))
    d = parse_decision("contact", '{"reply": "oi"}')
    assert apply_contact_decision(ws, item, d, NOW, mode="auto") == []


def test_takeover_by_number(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    customer(ws)
    run(ws, f"/assumir {f.ANA}", role="attendant", actor="self")
    record = load_state(ws, ANA)
    assert record.state is ContactState.PAUSED and record.human_takeover


def _draft(ws: Workspace, reply: str = "Custa R$ 490.") -> Draft:
    e = customer(ws)
    item = enqueue(ws, WorkItem.new("contact", f.ANA, (e["id"],), NOW))
    d = parse_decision("contact", json.dumps({"reply": reply}))
    draft = apply_contact_decision(ws, item, d, NOW, mode="assist")[0]
    assert isinstance(draft, Draft)
    return draft


def test_send_draft_as_written(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    draft = _draft(ws)
    result = run(ws, f"/enviar {draft.number}", role="attendant", actor="self")
    (reply,) = result.actions
    assert isinstance(reply, Reply) and reply.text == "Custa R$ 490." and reply.contact == f.ANA
    assert list_drafts(ws) == []


def test_send_draft_with_replacement_text(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    draft = _draft(ws)
    result = run(ws, f"/enviar {draft.number} Oi Ana, o anual sai por R$ 490 no pix.")
    (reply,) = result.actions
    assert isinstance(reply, Reply) and reply.text == "Oi Ana, o anual sai por R$ 490 no pix."


def test_send_unknown_draft_is_refused(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    result = run(ws, "/enviar 9")
    assert result.actions == [] and "9" in result.text


def test_a_blocked_draft_cannot_be_sent_as_is(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    customer(ws, f.BRUNO, name="Bruno Lima")
    draft = _draft(ws, reply=f"Ligue {f.BRUNO}")
    refused = run(ws, f"/enviar {draft.number}")
    assert refused.actions == [] and len(list_drafts(ws)) == 1
    ok = run(ws, f"/enviar {draft.number} Vou pedir para uma pessoa te ligar.")
    assert len(ok.actions) == 1


def test_contact_card_by_number_and_by_name_with_note_sources(tmp_path: Path) -> None:  # AC21
    ws = setup(tmp_path)
    customer(ws)
    run(ws, f"/nota {f.ANA} prefere pagar no pix", role="admin")
    run(ws, f"/nota {f.ANA} liga depois das 18h", role="attendant", actor="self")
    for query in (f.ANA, "Ana", "ana souza"):
        card = run(ws, f"/cliente {query}").text
        assert "prefere pagar no pix" in card and "liga depois das 18h" in card
        assert "admin" in card and "attendant" in card or "atendente" in card


def test_contact_lookup_without_a_match(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    assert "não encontrei" in run(ws, "/cliente Zé").text.lower()


def test_thread_returns_the_stored_lines(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    for i in range(5):
        customer(ws, text=f"mensagem {i}")
    out = run(ws, f"/conversa {f.ANA} 3").text
    assert "mensagem 4" in out and "mensagem 2" in out and "mensagem 0" not in out


def test_contacts_list_and_filter(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    customer(ws)
    customer(ws, f.BRUNO, text="oi", name="Bruno Lima")
    escalate(ws, f.BRUNO)
    everyone = run(ws, "/clientes").text
    assert f.ANA in everyone and f.BRUNO in everyone
    only = run(ws, "/clientes handoff").text
    assert f.BRUNO in only and f.ANA not in only


def test_admins_add_list_and_remove_with_confirmation(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    added = run(ws, f"/admin add {f.BRUNO} Bruno")
    assert Registry.load(ws).is_admin(f.BRUNO) and f.BRUNO in run(ws, "/admins").text
    assert added.actions == []
    first = run(ws, f"/admin remover {f.BRUNO}")
    assert Registry.load(ws).is_admin(f.BRUNO) and "/confirmar" in first.text
    code = first.text.split("/confirmar")[1].split()[0]
    second = run(ws, f"/confirmar {code}")
    assert not Registry.load(ws).is_admin(f.BRUNO) and second.text


def test_admin_add_the_connected_number_over_whatsapp_is_refused(tmp_path: Path) -> None:  # AC44
    ws = setup(tmp_path)
    result = run(ws, f"/admin add {f.CONNECTED}")
    assert "configurou" in result.text and not Registry.load(ws).connected_is_admin()


def test_last_admin_cannot_be_removed_via_whatsapp(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    first = run(ws, f"/admin remover {OWNER}")
    code = first.text.split("/confirmar")[1].split()[0]
    assert "último" in run(ws, f"/confirmar {code}").text
    assert Registry.load(ws).is_admin(OWNER)


def test_confirm_with_a_wrong_code_does_nothing(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    run(ws, f"/admin add {f.BRUNO}")
    run(ws, f"/admin remover {f.BRUNO}")
    assert (
        "código" in run(ws, "/confirmar 0000x").text.lower()
        or "inválido" in run(ws, "/confirmar 12").text.lower()
    )
    assert Registry.load(ws).is_admin(f.BRUNO)


def test_teach_files_internal_first_then_public_and_the_next_pack_uses_it(
    tmp_path: Path,
) -> None:  # AC21/AC49
    from tn_assist.pack import build_pack

    ws = setup(tmp_path)
    start = run(ws, "/ensinar Promoção de outubro")
    assert "Promoção" in start.text or "promocao" in start.text.lower()
    assert (
        capture_text(ws, OWNER, "Em outubro o plano anual tem 10% de desconto no pix.", NOW)
        is not None
    )
    assert capture_text(ws, OWNER, "Vale até 31/10.", NOW) is not None
    done = run(ws, "/pronto")
    assert "internal/promocao-de-outubro.md" in done.text and "/publico" in done.text
    assert (ws.root / "knowledge/internal/promocao-de-outubro.md").exists()
    e = customer(ws, text="tem desconto no plano anual no pix?")
    item = enqueue(ws, WorkItem.new("contact", f.ANA, (e["id"],), NOW))
    assert "10% de desconto" not in build_pack(ws, item, now=NOW).text  # still internal
    published = run(ws, "/publico")
    assert (ws.root / "knowledge/promocao-de-outubro.md").exists()
    assert not (ws.root / "knowledge/internal/promocao-de-outubro.md").exists()
    assert "promocao-de-outubro.md" in published.text
    assert "10% de desconto" in build_pack(ws, item, now=NOW).text


def test_capture_ignores_text_from_other_people_and_commands(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    run(ws, "/ensinar tema")
    assert capture_text(ws, f.BRUNO, "texto de outra pessoa", NOW) is None
    assert capture_text(ws, OWNER, "/status", NOW) is None
    assert capture_text(setup(tmp_path / "x"), OWNER, "sem captura ativa", NOW) is None


def test_kb_list_show_and_remove_with_confirmation(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    listing = run(ws, "/kb").text
    assert "pricing.md" in listing and "internal/margins.md" in listing
    assert "Plano anual" in run(ws, "/kb pricing").text
    first = run(ws, "/kb remover faq")
    assert (ws.root / "knowledge/faq.md").exists() and "/confirmar" in first.text
    code = first.text.split("/confirmar")[1].split()[0]
    run(ws, f"/confirmar {code}")
    assert not (ws.root / "knowledge/faq.md").exists()
    assert "faq.md" not in ws.path("knowledge", "INDEX.md").read_text()


def test_sources_list_and_change_class(tmp_path: Path) -> None:  # AC45
    from tn_assist.sources import SourceClass, SourceRegistry

    ws = setup(tmp_path)
    reg = SourceRegistry.load(ws)
    reg.discovered(["catalogo"])
    assert "catalogo" in run(ws, "/fontes").text
    first = run(ws, "/fontes catalogo publica")
    assert SourceRegistry.load(ws).get("catalogo").cls is SourceClass.OFF  # type: ignore[union-attr]
    code = first.text.split("/confirmar")[1].split()[0]
    run(ws, f"/confirmar {code}")
    assert SourceRegistry.load(ws).get("catalogo").cls is SourceClass.PUBLIC  # type: ignore[union-attr]
    run(ws, "/fontes catalogo desligada")  # narrowing needs no confirmation
    assert SourceRegistry.load(ws).get("catalogo").cls is SourceClass.OFF  # type: ignore[union-attr]


def test_forget_erases_the_contact_after_confirmation(tmp_path: Path) -> None:  # AC22
    ws = setup(tmp_path)
    escalate(ws)
    card = Card.load(ws, ANA)
    card.merge([Fact("interest", "plano anual", "admin", utc_iso(NOW))])
    card.save()
    first = run(ws, f"/esquecer {f.ANA}")
    assert (ws.root / "contacts" / f.ANA).exists() and "/confirmar" in first.text
    code = first.text.split("/confirmar")[1].split()[0]
    done = run(ws, f"/confirmar {code}")
    assert not (ws.root / "contacts" / f.ANA).exists()
    assert not (ws.root / "escalations" / f"{f.ANA}.json").exists()
    assert load_state(ws, ANA).state is ContactState.ACTIVE  # treated as new
    assert Card.load(ws, ANA).facts() == []
    assert "plataforma" in done.text.lower()
    audit = [json.loads(line) for line in ws.path("audit.log").read_text().splitlines()]
    assert audit[-1]["action"] == "forget" and audit[-1]["number"] == f.ANA


def test_mode_and_language_commands(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    run(ws, "/modo automático")
    assert load_config(ws)["mode"] == "auto"
    run(ws, "/mode assist")
    assert load_config(ws)["mode"] == "assist"
    assert "assistido" in run(ws, "/modo nada").text or "automático" in run(ws, "/modo nada").text
    run(ws, "/idioma en")
    assert load_config(ws)["lang"] == "en"
    assert "/lang" in run(ws, "/lang").text


def test_policy_shows_the_numbers(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    text = run(ws, "/politica").text
    assert "6000" in text and "90" in text


def test_digest_counts_people_by_state(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    customer(ws)
    escalate(ws, f.BRUNO)
    text = run(ws, "/resumo").text
    assert "2" in text


def test_pause_one_contact(tmp_path: Path) -> None:
    ws = setup(tmp_path)
    customer(ws)
    run(ws, f"/pausar {f.ANA}")
    assert load_state(ws, ANA).state is ContactState.PAUSED


def test_every_console_text_passes_the_copy_guards() -> None:
    from tests.support import copy_rules
    from tn_assist.i18n import STRINGS

    triples = [(k, lang, v) for k, d in STRINGS.items() for lang, v in d.items()]
    assert copy_rules.scan(triples) == []


@pytest.mark.parametrize("lang", ["pt", "en"])
def test_unknown_numbers_are_explained(tmp_path: Path, lang: str) -> None:
    ws = setup(tmp_path, lang=lang)
    out = run(ws, "/cliente 123").text
    assert out


def _opt_out(ws: Workspace, number: str = f.ANA) -> None:
    e = customer(ws, number, text="parar de receber")
    item = enqueue(ws, WorkItem.new("contact", number, (e["id"],), NOW))
    d = parse_decision("contact", '{"reply": "Tudo bem.", "state": "do_not_contact"}')
    apply_contact_decision(ws, item, d, NOW, mode="auto")


def test_an_attendant_cannot_lift_the_global_pause(tmp_path: Path) -> None:  # security review
    ws = setup(tmp_path)
    run(ws, "/pausar")
    result = run(ws, "/retomar todos", role="attendant", actor="self")
    assert load_config(ws)["paused"] is True and "administrador" in result.text
    run(ws, "/retomar todos")
    assert load_config(ws)["paused"] is False


def test_resume_never_brings_back_someone_who_opted_out(tmp_path: Path) -> None:  # security review
    ws = setup(tmp_path)
    _opt_out(ws)
    refused = run(ws, f"/retomar {f.ANA}", role="attendant", actor="self")
    assert (
        load_state(ws, ANA).state is ContactState.DO_NOT_CONTACT and "administrador" in refused.text
    )
    asked = run(ws, f"/retomar {f.ANA}")
    assert load_state(ws, ANA).state is ContactState.DO_NOT_CONTACT and "/confirmar" in asked.text
    code = asked.text.split("/confirmar")[1].split()[0]
    run(ws, f"/confirmar {code}")
    assert load_state(ws, ANA).state is ContactState.ACTIVE  # an admin, knowingly, with a code


def test_takeover_pause_and_native_messages_keep_the_opt_out(
    tmp_path: Path,
) -> None:  # security review
    ws = setup(tmp_path)
    _opt_out(ws)
    run(ws, f"/assumir {f.ANA}", role="attendant", actor="self")
    assert load_state(ws, ANA).state is ContactState.DO_NOT_CONTACT
    run(ws, f"/pausar {f.ANA}")
    assert load_state(ws, ANA).state is ContactState.DO_NOT_CONTACT
    ingest(ws, parse_events(f.payload(f.native_outbound_to_contact(preview="Oi!"))), NOW)
    record = load_state(ws, ANA)
    assert record.state is ContactState.DO_NOT_CONTACT and record.human_takeover is True


def test_drafts_of_a_contact_who_opts_out_are_dropped_and_cannot_be_sent(
    tmp_path: Path,
) -> None:  # security review
    ws = setup(tmp_path)
    draft = _draft(ws)
    assert len(list_drafts(ws)) == 1
    _opt_out(ws)
    assert list_drafts(ws) == []
    result = run(ws, f"/enviar {draft.number}")
    assert result.actions == []


def test_a_stale_draft_for_an_opted_out_contact_is_refused(
    tmp_path: Path,
) -> None:  # security review
    ws = setup(tmp_path)
    draft = _draft(ws)
    # opt-out recorded behind the draft's back (state written directly)
    from tn_assist.state import save_state

    save_state(ws, ANA, replace(load_state(ws, ANA), state=ContactState.DO_NOT_CONTACT))
    result = run(ws, f"/enviar {draft.number} Oi de novo")
    assert result.actions == [] and "pediu para não receber" in result.text
    assert list_drafts(ws) == []


def test_while_paused_only_an_admin_can_send_a_draft(tmp_path: Path) -> None:  # security review
    ws = setup(tmp_path)
    draft = _draft(ws)
    run(ws, "/pausar")
    refused = run(ws, f"/enviar {draft.number}", role="attendant", actor="self")
    assert refused.actions == [] and len(list_drafts(ws)) == 1
    assert len(run(ws, f"/enviar {draft.number}").actions) == 1
