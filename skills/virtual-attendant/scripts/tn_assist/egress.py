"""The egress guard: what may leave toward one customer.

Deterministic and conservative. A reply is refused when it contains another person's number or
name, text from a non-public source, a credential, or a personal-data pattern that the customer
did not supply and public knowledge does not publish. A refusal is safe by design: the caller turns
it into a hand-off, never into a retry.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from tn_assist.config import admin_numbers
from tn_assist.memory import Card, read_lines
from tn_assist.paths import ContactKey, InvalidContactKey, Workspace
from tn_assist.sources import SourceRegistry

SHINGLE = 5
_NUMBERISH = re.compile(r"\+?\d(?:[\d\s().-]{6,}\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CREDENTIAL = re.compile(r"\btn_(?:live|oat|oup|ort)_[A-Za-z0-9_-]{8,}", re.IGNORECASE)
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class EgressVerdict:
    ok: bool
    reasons: tuple[str, ...] = ()


def _fold(text: str) -> str:
    base = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in base if not unicodedata.combining(ch))


def _words(text: str) -> list[str]:
    return _WORD.findall(_fold(text))


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = _words(text)
    return {tuple(words[i : i + SHINGLE]) for i in range(max(0, len(words) - SHINGLE + 1))}


def _digit_runs(text: str) -> list[str]:
    runs = []
    for match in _NUMBERISH.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 8 <= len(digits) <= 19:
            runs.append(digits)
    return runs


def _same_person(digits: str, number: str) -> bool:
    return digits.endswith(number) or number.endswith(digits)


def _canonical(digits: str) -> str:
    try:
        return str(ContactKey.parse(digits))
    except InvalidContactKey:
        return digits


def _read(ws: Workspace, *parts: str) -> str:
    try:
        return ws.path(*parts).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return ""


def _knowledge_texts(ws: Workspace) -> tuple[str, str]:
    """(public text, non-public text) of the knowledge base."""
    public: list[str] = []
    internal: list[str] = []
    root = ws.path("knowledge")
    if root.is_dir():
        for path in sorted(root.rglob("*.md")):
            relative = path.relative_to(root).parts
            target = internal if relative[0] == "internal" else public
            target.append(path.read_text(encoding="utf-8"))
    return "\n".join(public), "\n".join(internal)


def _other_contacts(ws: Workspace, contact: ContactKey) -> tuple[set[str], set[str]]:
    numbers: set[str] = set(admin_numbers(ws))
    names: set[str] = set()
    contacts = ws.root / "contacts"
    if contacts.is_dir():
        for directory in contacts.iterdir():
            if not directory.is_dir() or directory.name == str(contact):
                continue
            numbers.add(directory.name)
            try:
                card = Card.load(ws, ContactKey.parse(directory.name))
            except InvalidContactKey:
                continue
            fact = card.get("name")
            if fact and len(fact.value) >= 4:
                names.add(fact.value)
    return numbers, names


def check_egress(ws: Workspace, text: str, *, contact: ContactKey) -> EgressVerdict:
    reasons: set[str] = set()
    public_text, internal_text = _knowledge_texts(ws)
    card = Card.load(ws, contact)
    own_text = " ".join(f.value for f in card.facts()) + " " + str(contact)
    own_text += " " + " ".join(ln.text for ln in read_lines(ws, contact) if ln.role == "contact")
    allowed_digits = {_canonical(d) for d in _digit_runs(public_text + " " + own_text)}
    allowed_digits.add(str(contact))
    allowed_mail = {m.lower() for m in _EMAIL.findall(public_text + " " + own_text)}
    others, other_names = _other_contacts(ws, contact)

    for digits in _digit_runs(text):
        canonical = _canonical(digits)
        if canonical in allowed_digits or any(_same_person(canonical, a) for a in allowed_digits):
            continue
        if any(_same_person(canonical, n) for n in others):
            reasons.add("other_contact")
        else:
            reasons.add("personal_data")
    for mail in _EMAIL.findall(text):
        if mail.lower() not in allowed_mail:
            reasons.add("personal_data")

    folded = _fold(text)
    own_names = _fold(own_text + " " + public_text)
    for name in other_names:
        pattern = r"\b" + re.escape(_fold(name)) + r"\b"
        if re.search(pattern, folded) and not re.search(pattern, own_names):
            reasons.add("other_contact")

    markers = SourceRegistry.load(ws).non_public_markers()
    if any(m.lower() in text.lower() for m in markers):
        reasons.add("non_public")
    if internal_text:
        hidden = _shingles(internal_text) - _shingles(public_text)
        if hidden & _shingles(text):
            reasons.add("non_public")

    if _CREDENTIAL.search(text):
        reasons.add("credential")
    return EgressVerdict(not reasons, tuple(sorted(reasons)))
