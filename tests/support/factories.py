"""Event factories shaped like the real `GET /v1/events` items (numbers and names are fake)."""

from __future__ import annotations

import itertools
from typing import Any

ORG = "org_" + "a" * 32
NUM = "num_" + "b" * 32
OTHER_NUM = "num_" + "c" * 32
CONNECTED = "5511999990001"
ANA = "5511977776666"
BRUNO = "5511966665555"
OWNER = "5511955554444"
GROUP = "grp_" + "d" * 32

_counter = itertools.count(1)


def event_id() -> str:
    return "evt_" + format(next(_counter), "032x")


def _party(number: str, name: str, alias: str | None = None) -> dict[str, Any]:
    party: dict[str, Any] = {
        "contact_id": "ct_" + number.zfill(32)[-32:],
        "name": name,
        "number": number,
    }
    if alias:
        party["alias"] = alias
    return party


def make_event(
    *,
    type: str = "message.received",
    origin: str = "native",
    direction: str = "inbound",
    sender: str = ANA,
    sender_name: str = "Ana Souza",
    recipient: str | None = None,
    group: str | None = None,
    content_type: str = "text",
    preview: str = "Olá, quero saber o preço",
    number_id: str = NUM,
    connected: str = CONNECTED,
    eid: str | None = None,
    created_at: str = "2026-10-17T12:00:00Z",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one event. `sender` and `recipient` are phone numbers (the connected one has an alias)."""
    if direction == "inbound":
        frm = _party(sender, sender_name, "main" if sender == connected else None)
        if group:
            to: dict[str, Any] = {"group_id": group, "name": "Clientes"}
        else:
            to = _party(
                recipient or connected,
                "Loja",
                "main" if (recipient or connected) == connected else None,
            )
    else:
        frm = _party(sender if sender != ANA else connected, "Loja", "main")
        if group:
            to = {"group_id": group, "name": "Clientes"}
        else:
            to = _party(recipient or ANA, "Ana Souza")
    data: dict[str, Any] = {
        "id": "msg_" + format(next(_counter), "032x"),
        "from": frm,
        "to": to,
        "preview": preview,
        "content_type": content_type,
        "direction": direction,
        "channel": "whatsapp",
        "origin": origin,
        "at": created_at,
    }
    if extra:
        data.update(extra)
    return {
        "id": eid or event_id(),
        "type": type,
        "organization_id": ORG,
        "number_alias": "main",
        "number_id": number_id,
        "created_at": created_at,
        "data": data,
    }


def inbound_text(**kw: Any) -> dict[str, Any]:
    return make_event(**kw)


def inbound_group(**kw: Any) -> dict[str, Any]:
    kw.setdefault("group", GROUP)
    return make_event(**kw)


def native_outbound_to_contact(**kw: Any) -> dict[str, Any]:
    return make_event(
        type="message.sent", origin="native", direction="outbound", sender=CONNECTED, **kw
    )


def self_chat_native(
    *, direction: str = "outbound", text: str = "/ajuda", **kw: Any
) -> dict[str, Any]:
    return make_event(
        type="message.sent" if direction == "outbound" else "message.received",
        origin="native",
        direction=direction,
        sender=CONNECTED,
        recipient=CONNECTED,
        preview=text,
        **kw,
    )


def tn_outbound(**kw: Any) -> dict[str, Any]:
    return make_event(
        type="message.sent", origin="tn", direction="outbound", sender=CONNECTED, **kw
    )


def payload(*events: dict[str, Any], has_more: bool = False) -> dict[str, Any]:
    """An API page: newest first, as `GET /v1/events` returns it."""
    return {"object": "list", "data": list(events), "has_more": has_more}
