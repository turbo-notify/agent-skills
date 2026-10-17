from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.support.workspace import make_workspace
from tn_assist.commands import (
    AUTHORIZATION,
    Command,
    authorize,
    confirm,
    issue_confirmation,
    needs_confirmation,
    parse_command,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("text", "name", "args"),
    [
        ("/ajuda", "help", ()),
        ("/help", "help", ()),
        ("  /Ajuda  ", "help", ()),
        ("/fila", "queue", ()),
        ("/queue", "queue", ()),
        ("/retomar 2", "resume", ("2",)),
        ("/resume 5511977776666", "resume", ("5511977776666",)),
        ("/retomar todos", "resume", ("todos",)),
        ("/assumir 1", "takeover", ("1",)),
        ("/takeover 1", "takeover", ("1",)),
        ("/cliente Ana Souza", "contact", ("Ana", "Souza")),
        ("/contact 5511977776666", "contact", ("5511977776666",)),
        ("/conversa 5511977776666 20", "thread", ("5511977776666", "20")),
        (
            "/nota 5511977776666 prefere pagar no pix",
            "note",
            ("5511977776666", "prefere", "pagar", "no", "pix"),
        ),
        ("/clientes pausado", "contacts", ("pausado",)),
        ("/admins", "admins", ()),
        ("/admin add 5511955554444 Bruno", "admin_add", ("5511955554444", "Bruno")),
        ("/admin adicionar 5511955554444", "admin_add", ("5511955554444",)),
        ("/admin remover 5511955554444", "admin_remove", ("5511955554444",)),
        ("/admin remove 5511955554444", "admin_remove", ("5511955554444",)),
        ("/ensinar preços", "teach", ("preços",)),
        ("/teach pricing", "teach", ("pricing",)),
        ("/pronto", "done", ()),
        ("/publico", "public", ()),
        ("/público", "public", ()),
        ("/interno", "internal", ()),
        ("/conhecimento", "kb", ()),
        ("/kb remover pricing", "kb", ("remover", "pricing")),
        ("/fontes", "sources", ()),
        ("/pausar", "pause", ()),
        ("/pausar 5511977776666", "pause", ("5511977776666",)),
        ("/esquecer 5511977776666", "forget", ("5511977776666",)),
        ("/modo assistido", "mode", ("assistido",)),
        ("/mode auto", "mode", ("auto",)),
        ("/politica", "policy", ()),
        ("/política", "policy", ()),
        ("/idioma en", "lang", ("en",)),
        ("/enviar 3", "send", ("3",)),
        ("/send 3 Olá, tudo bem?", "send", ("3", "Olá,", "tudo", "bem?")),
        ("/resumo", "digest", ()),
        ("/status", "status", ()),
        ("/confirmar 4821", "confirm", ("4821",)),
    ],
)
def test_parses_both_languages(text: str, name: str, args: tuple[str, ...]) -> None:
    cmd = parse_command(text)
    assert cmd is not None and cmd.name == name and cmd.args == args


@pytest.mark.parametrize("text", ["oi", "", "ajuda", "/", "/naoexiste", "quero /ajuda", "//ajuda"])
def test_not_a_command(text: str) -> None:
    assert parse_command(text) is None


def test_admin_without_subcommand_is_the_list() -> None:
    cmd = parse_command("/admin")
    assert cmd is not None and cmd.name == "admins"


def test_send_keeps_the_replacement_text_verbatim() -> None:
    cmd = parse_command("/enviar 3   Olá!   Tudo bem?")
    assert cmd is not None and cmd.rest == "Olá!   Tudo bem?"


def test_authorization_matches_the_spec_table() -> None:
    both = {
        "help",
        "status",
        "queue",
        "send",
        "digest",
        "resume",
        "takeover",
        "contact",
        "thread",
        "note",
    }
    admin_only = {
        "contacts",
        "admins",
        "admin_add",
        "admin_remove",
        "teach",
        "done",
        "public",
        "internal",
        "kb",
        "sources",
        "pause",
        "forget",
        "mode",
        "policy",
        "lang",
        "confirm",
    }
    for name in both:
        assert AUTHORIZATION[name] == frozenset({"admin", "attendant"}), name
    for name in admin_only:
        assert AUTHORIZATION[name] == frozenset({"admin"}), name
    assert set(AUTHORIZATION) == both | admin_only


def test_authorize() -> None:
    assert authorize(Command("forget", ("5511977776666",), "/esquecer 5511977776666"), "admin")
    assert not authorize(
        Command("forget", ("5511977776666",), "/esquecer 5511977776666"), "attendant"
    )
    assert authorize(Command("resume", (), "/retomar"), "attendant")


def test_which_commands_need_confirmation() -> None:
    assert needs_confirmation(parse_command("/esquecer 5511977776666"))  # type: ignore[arg-type]
    assert needs_confirmation(parse_command("/admin remover 5511955554444"))  # type: ignore[arg-type]
    assert needs_confirmation(parse_command("/kb remover pricing"))  # type: ignore[arg-type]
    assert not needs_confirmation(parse_command("/kb pricing"))  # type: ignore[arg-type]
    assert not needs_confirmation(parse_command("/ajuda"))  # type: ignore[arg-type]


def test_confirmation_is_for_the_same_actor_and_expires(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    cmd = parse_command("/esquecer 5511977776666")
    assert cmd is not None
    code = issue_confirmation(ws, "5511955554444", cmd, NOW)
    assert code.isdigit() and len(code) == 4
    assert confirm(ws, "5511900000000", code, NOW) is None  # someone else
    assert confirm(ws, "5511955554444", "0000" if code != "0000" else "1111", NOW) is None
    again = confirm(ws, "5511955554444", code, NOW + timedelta(minutes=1))
    assert again is not None and again.name == "forget"
    assert confirm(ws, "5511955554444", code, NOW + timedelta(minutes=1)) is None  # single use


def test_confirmation_expires_after_ten_minutes(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    cmd = parse_command("/esquecer 5511977776666")
    assert cmd is not None
    code = issue_confirmation(ws, "5511955554444", cmd, NOW)
    assert confirm(ws, "5511955554444", code, NOW + timedelta(minutes=11)) is None
