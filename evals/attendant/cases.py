"""The fictitious shop and the customers' messages the evaluation uses.

Everything here is invented: the business, its catalog, the customers' names and numbers. The files
live in ``fixtures/`` and are plain data so they can be read, reviewed and edited by a person.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from evals.attendant.checker import Knowledge
from tn_assist.knowledge import Visibility

FIXTURES = Path(__file__).resolve().parent / "fixtures"

UNSUPPORTED = "unsupported"
CONTROL = "control"


@dataclass(frozen=True)
class Case:
    """One customer message. ``unsupported`` cases have no answer in the knowledge."""

    id: str
    group: str
    category: str
    lang: str
    name: str
    number: str
    message: str
    must_include: tuple[str, ...] = ()

    @property
    def unsupported(self) -> bool:
        return self.group == UNSUPPORTED


@dataclass(frozen=True)
class Turn:
    """One message of a conversation and what it is owed.

    By default the customer is owed an answer in their language. ``silent`` says the message only
    closes the conversation, so the right outcome is no reply and no hand-off. ``must_include``
    patterns (folded text) say what an answer has to state, as for a control.
    """

    lang: str
    message: str
    ambiguous: bool = False
    silent: bool = False
    must_include: tuple[str, ...] = ()


@dataclass(frozen=True)
class Conversation:
    id: str
    name: str
    number: str
    turns: tuple[Turn, ...]


@dataclass(frozen=True)
class ShopFile:
    path: str
    visibility: Visibility
    description: str
    text: str


@dataclass(frozen=True)
class Shop:
    business: str
    lang: str
    connected_number: str
    admin_number: str
    files: tuple[ShopFile, ...]
    markers: tuple[str, ...] = field(default_factory=tuple)

    def knowledge(self) -> Knowledge:
        public = "\n".join(f.text for f in self.files if f.visibility == "public")
        internal = "\n".join(f.text for f in self.files if f.visibility == "internal")
        return Knowledge(public, internal, self.markers)


def _read(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_shop(root: Optional[Path] = None) -> Shop:
    base = root or FIXTURES / "shop"
    data = json.loads((base / "shop.json").read_text(encoding="utf-8"))
    files = tuple(
        ShopFile(
            item["path"],
            item["visibility"],
            item["description"],
            (base / item["source"]).read_text(encoding="utf-8"),
        )
        for item in data["files"]
    )
    return Shop(
        data["business"],
        data["lang"],
        data["connected_number"],
        data["admin_number"],
        files,
        tuple(data.get("internal_markers", ())),
    )


def load_cases() -> list[Case]:
    return [
        Case(
            id=item["id"],
            group=item["group"],
            category=item["category"],
            lang=item["lang"],
            name=item["name"],
            number=item["number"],
            message=item["message"],
            must_include=tuple(item.get("must_include", ())),
        )
        for item in _read("cases.json")
    ]


def load_conversations() -> list[Conversation]:
    return [
        Conversation(
            id=item["id"],
            name=item["name"],
            number=item["number"],
            turns=tuple(
                Turn(
                    t["lang"],
                    t["message"],
                    bool(t.get("ambiguous", False)),
                    bool(t.get("silent", False)),
                    tuple(t.get("must_include", ())),
                )
                for t in item["turns"]
            ),
        )
        for item in _read("conversations.json")
    ]
