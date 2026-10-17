from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.support import factories as f
from tests.support.workspace import make_workspace
from tn_assist.admins import (
    ConnectedNumberNeedsConfigurator,
    ConnectedNumberNeedsConfirmation,
    LastAdministratorError,
    NotAnAdministrator,
    Registry,
)
from tn_assist.paths import Workspace
from tn_assist.store import read_jsonl

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def fresh(tmp_path: Path) -> Workspace:
    ws = make_workspace(tmp_path, admins=())
    return ws


def test_first_admin_only_via_configurator(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    with pytest.raises(NotAnAdministrator):
        reg.add(f.OWNER, "Dona", by=f.BRUNO, via="whatsapp", now=NOW)
    reg.add(f.OWNER, "Dona", by="configurator", via="configurator", now=NOW)
    assert Registry.load(ws).is_admin(f.OWNER)


def test_admin_can_add_another_over_whatsapp(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    reg.add(f.BRUNO, "Bruno", by=f.OWNER, via="whatsapp", message_id="msg_1", now=NOW)
    assert Registry.load(ws).numbers() == frozenset({f.OWNER, f.BRUNO})


def test_non_admin_cannot_add_over_whatsapp(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    with pytest.raises(NotAnAdministrator):
        reg.add(f.BRUNO, None, by=f.ANA, via="whatsapp", now=NOW)


def test_last_admin_cannot_be_removed(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    with pytest.raises(LastAdministratorError):
        reg.remove(f.OWNER, by=f.OWNER, now=NOW)
    reg.add(f.BRUNO, None, by=f.OWNER, via="whatsapp", now=NOW)
    reg.remove(f.OWNER, by=f.BRUNO, now=NOW)
    assert Registry.load(ws).numbers() == frozenset({f.BRUNO})


def test_number_spellings_make_one_entry(tmp_path: Path) -> None:  # RF 2
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    assert reg.add("+55 (11) 95555-4444", None, by="configurator", via="configurator", now=NOW)
    assert reg.add("5511955554444", None, by="configurator", via="configurator", now=NOW) is False
    assert len(Registry.load(ws).numbers()) == 1


def test_audit_log_records_actor_channel_and_message_id(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    reg.add(f.BRUNO, None, by=f.OWNER, via="whatsapp", message_id="msg_9", now=NOW)
    reg.remove(f.BRUNO, by=f.OWNER, message_id="msg_10", now=NOW)
    rows = read_jsonl(ws.path("audit.log"))
    assert [r["action"] for r in rows] == ["add", "add", "remove"]
    assert (
        rows[1]["by"] == f.OWNER
        and rows[1]["via"] == "whatsapp"
        and rows[1]["message_id"] == "msg_9"
    )
    assert rows[0]["at"] == "2026-10-17T12:00:00Z"


def test_connected_number_via_whatsapp_is_refused_and_unchanged(tmp_path: Path) -> None:  # AC44
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    with pytest.raises(ConnectedNumberNeedsConfigurator):
        reg.add(f.CONNECTED, None, by=f.OWNER, via="whatsapp", now=NOW)
    assert Registry.load(ws).numbers() == frozenset({f.OWNER})


def test_connected_number_via_configurator_needs_confirmation_then_succeeds(
    tmp_path: Path,
) -> None:  # AC43
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    with pytest.raises(ConnectedNumberNeedsConfirmation):
        reg.add(f.CONNECTED, None, by="configurator", via="configurator", now=NOW)
    assert Registry.load(ws).connected_is_admin() is False
    reg.add(
        f.CONNECTED, None, by="configurator", via="configurator", confirmed_connected=True, now=NOW
    )
    assert Registry.load(ws).connected_is_admin() is True
    rows = read_jsonl(ws.path("audit.log"))
    assert rows[-1]["connected_number_confirmed"] is True


def test_declining_leaves_registry_unchanged(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    before = ws.path("admins.json").read_text()
    with pytest.raises(ConnectedNumberNeedsConfirmation):
        reg.add(f.CONNECTED, None, by="configurator", via="configurator", now=NOW)
    assert ws.path("admins.json").read_text() == before


def test_admin_can_remove_the_connected_number(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    reg.add(
        f.CONNECTED, None, by="configurator", via="configurator", confirmed_connected=True, now=NOW
    )
    reg.remove(f.CONNECTED, by=f.OWNER, now=NOW)
    assert Registry.load(ws).connected_is_admin() is False


def test_remove_unknown_is_a_no_op(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    assert reg.remove(f.BRUNO, by=f.OWNER, now=NOW) is False


def test_garbage_number_is_refused(tmp_path: Path) -> None:
    from tn_assist.paths import InvalidContactKey

    ws = fresh(tmp_path)
    with pytest.raises(InvalidContactKey):
        Registry.load(ws).add("abc", None, by="configurator", via="configurator", now=NOW)


def test_non_admin_cannot_remove(tmp_path: Path) -> None:
    ws = fresh(tmp_path)
    reg = Registry.load(ws)
    reg.add(f.OWNER, None, by="configurator", via="configurator", now=NOW)
    reg.add(f.BRUNO, None, by=f.OWNER, via="whatsapp", now=NOW)
    with pytest.raises(NotAnAdministrator):
        reg.remove(f.BRUNO, by=f.ANA, now=NOW)
    assert Registry.load(ws).is_admin(f.BRUNO)
