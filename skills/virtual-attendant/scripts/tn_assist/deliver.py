"""From abstract actions to concrete deliveries.

Each delivery is one call that trusted code makes: ``reply`` (``reply_to_message`` or its REST twin)
or ``send`` (``send_text``). The model never makes these calls. Idempotency keys are derived from
the work item, the recipient and the text, so a retry after a crash sends nothing twice.
"""

from __future__ import annotations

import hashlib
from typing import Any

from tn_assist.admins import Registry
from tn_assist.apply import Action, Draft, NotifyAdmins, NotifySelfChat, Reply, Say
from tn_assist.config import load_config
from tn_assist.i18n import Lang, t
from tn_assist.memory import Card
from tn_assist.paths import ContactKey, InvalidContactKey, Workspace


def _key(seed: str, to: str, text: str) -> str:
    digest = hashlib.sha1(f"{seed}|{to}|{text}".encode()).hexdigest()
    return f"n-{digest[:32]}"


def _name(ws: Workspace, number: str) -> str:
    try:
        fact = Card.load(ws, ContactKey.parse(number)).get("name")
    except InvalidContactKey:
        return number
    return fact.value if fact else number


def expand(ws: Workspace, actions: list[Action], *, seed: str) -> list[dict[str, Any]]:
    config = load_config(ws)
    connected = str(config.get("connected_number", ""))
    admins = sorted(Registry.load(ws).numbers())
    lang: Lang = "en" if config.get("lang") == "en" else "pt"
    ops: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def send(to: str, text: str) -> None:
        if (to, text) in seen:
            return
        seen.add((to, text))
        ops.append({"op": "send", "to": to, "text": text, "idempotency_key": _key(seed, to, text)})

    def to_people(text: str) -> None:
        for number in admins:
            send(number, text)
        if connected:
            send(connected, text)

    for action in actions:
        if isinstance(action, Reply):
            ops.append(
                {
                    "op": "reply",
                    "message_id": action.to_message_id,
                    "text": action.text,
                    "idempotency_key": action.idempotency_key,
                    "contact": action.contact,
                }
            )
        elif isinstance(action, (NotifyAdmins, NotifySelfChat)):
            to_people(action.text)
        elif isinstance(action, Say):
            send(connected if action.to == "self" else action.to, action.text)
        elif isinstance(action, Draft):
            to_people(
                t(
                    "draft_notice",
                    lang,
                    n=str(action.number),
                    name=_name(ws, action.contact),
                    number=action.contact,
                    text=action.text,
                )
            )
    return ops
