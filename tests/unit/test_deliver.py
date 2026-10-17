from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tests.support import factories as f
from tests.support.workspace import make_workspace
from tn_assist.admins import Registry
from tn_assist.apply import Draft, NotifyAdmins, NotifySelfChat, Reply, Say
from tn_assist.deliver import expand

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def test_reply_becomes_a_reply_operation(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    ops = expand(ws, [Reply(f.ANA, "msg_1", "Olá!", "evt_1")], seed="item-1")
    assert ops == [
        {
            "op": "reply",
            "message_id": "msg_1",
            "text": "Olá!",
            "idempotency_key": "evt_1",
            "contact": f.ANA,
        }
    ]


def test_notices_go_to_every_admin_and_to_the_self_chat(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, admins=(f.OWNER, f.BRUNO))
    ops = expand(ws, [NotifyAdmins("atenção"), NotifySelfChat("atenção")], seed="item-1")
    assert {o["to"] for o in ops} == {f.OWNER, f.BRUNO, f.CONNECTED}
    assert all(o["op"] == "send" and o["text"] == "atenção" for o in ops)
    assert len({o["idempotency_key"] for o in ops}) == 3


def test_when_the_connected_number_is_an_admin_the_notice_goes_out_once(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, admins=(f.OWNER,))
    Registry.load(ws).add(
        f.CONNECTED, None, by="configurator", via="configurator", confirmed_connected=True, now=NOW
    )
    ops = expand(ws, [NotifyAdmins("x"), NotifySelfChat("x")], seed="item-1")
    assert sorted(o["to"] for o in ops) == sorted([f.OWNER, f.CONNECTED])


def test_say_to_a_person_and_to_the_self_chat(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    ops = expand(ws, [Say(f.OWNER, "oi"), Say("self", "tudo certo")], seed="item-2")
    assert [(o["to"], o["text"]) for o in ops] == [(f.OWNER, "oi"), (f.CONNECTED, "tudo certo")]


def test_a_draft_is_shown_to_the_people_with_how_to_approve_it(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    draft = Draft(3, f.ANA, "Custa R$ 490.", "evt_1", "msg_1")
    ops = expand(ws, [draft], seed="item-3")
    assert {o["to"] for o in ops} == {f.OWNER, f.CONNECTED}
    text = ops[0]["text"]
    assert "#3" in text and "Custa R$ 490." in text and "/enviar 3" in text


def test_the_same_input_gives_the_same_keys(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    a = expand(ws, [NotifyAdmins("x")], seed="item-1")
    b = expand(ws, [NotifyAdmins("x")], seed="item-1")
    c = expand(ws, [NotifyAdmins("x")], seed="item-9")
    assert a == b and a != c


def test_keys_are_printable_ascii_up_to_255(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    for op in expand(ws, [NotifyAdmins("é" * 400)], seed="x" * 400):
        key = op["idempotency_key"]
        assert 1 <= len(key) <= 255 and key.isascii() and key.isprintable()
