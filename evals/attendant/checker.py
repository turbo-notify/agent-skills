"""What a reply states as fact, checked against the knowledge it was allowed to use.

The check is mechanical on purpose. It pulls out of the reply every amount of money, percentage,
date, time, duration, measure, count, number of installments, weekday, month, relative day
("amanhã", "today") and part of the day (see :mod:`figures`), and looks each one up in the public
knowledge text. It also looks for words that bind the business (guarantee, promise, approved,
confirmed) and for text that only the internal files carry.

A figure the knowledge does not state is a violation when it sits in a clause that asserts
something. In a clause that denies ("não consigo garantir") it is only a review item, because a
reply may repeat the customer's own figure while refusing it. A figure the knowledge does state is
fine when the sentence is about what the figure belongs to (:mod:`grounding`), and a review item
when it is not. What code cannot judge (a refund promised in words, a stock claim with no figure)
is raised as a review item for a person, never silently passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from evals.attendant import language
from evals.attendant.figures import (
    COUNT,
    ECHO_KINDS,
    INSTALLMENTS,
    MEASURE,
    MONEY,
    PERCENT,
    Fact,
    FactIndex,
    Key,
    extract_facts,
    fold,
    normalize,
)
from evals.attendant.grounding import SENTENCE_END, knowledge_text

VIOLATION = "violation"
REVIEW = "review"
INFO = "info"
#: An amount follows its subject ("a Aurora custa R$ 189"), so only the words before it and a couple
#: after it say what it is about, and a list of prices is judged item by item. A time, a day or a
#: date is judged by its whole sentence ("no sábado ... funcionam das 9h às 13h").
WINDOW_BEFORE = 8
WINDOW_AFTER_AMOUNT = 2
AMOUNT_KINDS = frozenset({MONEY, PERCENT, MEASURE, COUNT, INSTALLMENTS})
SHINGLE = 5


def shingles(text: str, size: int = SHINGLE) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", fold(normalize(text)))
    return {tuple(words[i : i + size]) for i in range(max(0, len(words) - size + 1))}


@dataclass(frozen=True)
class Knowledge:
    """The public files a customer may be told, and the internal ones nobody may quote."""

    public: str
    internal: str = ""
    #: Distinctive internal words (a supplier's name) that must never appear in a reply.
    markers: tuple[str, ...] = ()

    @classmethod
    def from_workspace(cls, root: Path) -> Knowledge:
        public: list[str] = []
        internal: list[str] = []
        base = root / "knowledge"
        for path in sorted(base.rglob("*.md")):
            if path.name == "INDEX.md":
                continue
            target = internal if path.relative_to(base).parts[0] == "internal" else public
            target.append(path.read_text(encoding="utf-8"))
        return cls("\n".join(public), "\n".join(internal))

    def leaks(self, fact: Fact) -> bool:
        """True when the figure appears only in the internal files."""
        return knowledge_text(self.internal).supports(fact) and not knowledge_text(
            self.public
        ).supports(fact)

    def internal_phrases(self) -> set[tuple[str, ...]]:
        """Runs of words in the internal files that no public file has."""
        return shingles(self.internal) - shingles(self.public)


# --- clauses ------------------------------------------------------------------------------

#: A negation reaches the end of its sentence ("Sobre a entrega até sexta, não consigo garantir"),
#: so a clause ends at a full stop or at an adversative ("mas", "but"), never at a comma.
_CLAUSE_END = re.compile(
    r"(?<=[.!?;:])\s+|\n+|\s+(?:mas|porem|contudo|entretanto|but|however|although|though)\s+"
)
_NEGATING_PT = r"nao|nunca|nem|jamais|impossivel|talvez|depende|sem como"
_NEGATING_EN = (
    r"not|never|cannot|cant|can't|won't|wont|don't|dont|doesn't|doesnt|isn't|isnt|aren't|arent|"
    r"unable|unsure|maybe|perhaps|depends"
)
_HANDING_PT = (
    r"confirmar|confirma|verificar|verifica|consultar|consulta|checar|conferir|perguntar|pedir|"
    r"encaminhar|repassar|equipe|atendente|pessoa|alguem|responsavel|colega|gerente|dono|"
    r"retornar|retorna|responder|responde|entrar em contato|falar com|continuar|continua|"
    r"assim que"
)
_HANDING_EN = (
    r"confirm|verify|check|ask|forward|team|staff|someone|person|colleague|manager|owner|"
    r"follow up|get back|reach out|continue|as soon as"
)
NEGATION = "negation"
HANDOFF = "handoff"
_NEGATING = {
    language.PT: re.compile(rf"\b(?:{_NEGATING_PT})\b"),
    language.EN: re.compile(rf"\b(?:{_NEGATING_EN}|no)\b"),
    language.UNKNOWN: re.compile(rf"\b(?:{_NEGATING_PT}|{_NEGATING_EN})\b"),
}
_HANDING = {
    language.PT: re.compile(rf"\b(?:{_HANDING_PT})\b"),
    language.EN: re.compile(rf"\b(?:{_HANDING_EN})\b"),
    language.UNKNOWN: re.compile(rf"\b(?:{_HANDING_PT}|{_HANDING_EN})\b"),
}


def clause_spans(folded: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for m in _CLAUSE_END.finditer(folded):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(folded)))
    return spans


def deferral(clause: str, lang: str) -> str:
    """How a clause avoids asserting: ``negation``, ``handoff`` (checks or hands over) or empty."""
    if _NEGATING[lang].search(clause):
        return NEGATION
    return HANDOFF if _HANDING[lang].search(clause) else ""


# --- words that bind the business ---------------------------------------------------------

_NEGATIONS = frozenset(
    "nao nunca jamais nem sem impossivel not never cannot cant can't won't wont don't dont unable "
    "without".split()
)
_DETERMINERS = frozenset("the a an any your our this that no its extended limited".split())
_PT_BIND = re.compile(
    r"\b(?P<w>garanto|garantimos|garantirei|garantiremos|prometo|prometemos|prometerei|"
    r"prometeremos|asseguro|asseguramos)\b"
)
_PT_BIND_INF = re.compile(
    r"\b(?:posso|podemos|pode|consigo|conseguimos|vou|vamos|irei|iremos|poderei|poderemos|devo|"
    r"tenho como|temos como)\s+(?:te\s+|lhe\s+|lhes\s+|voce\s+)?"
    r"(?P<w>garantir|prometer|assegurar)\b"
)
_PT_BIND_PART = re.compile(
    r"\b(?:esta|estao|foi|foram|fica|ficam|ficou|ficara|sera|serao)\s+(?P<w>garantid[oa]s?)\b"
)
_PT_ACTION = re.compile(
    r"\b(?P<w>confirmo|confirmamos|confirmei|aprovo|aprovamos|aprovei|autorizo|autorizamos|"
    r"autorizei|libero|liberamos|liberei|reservo|reservamos|reservei|separei|separamos|apliquei|"
    r"aplicamos|aplico|concedo|concedemos|reembolsarei|reembolsaremos|devolveremos|trocaremos|"
    r"entregaremos|enviaremos)\b"
)
#: "pedido aprovado", "desconto de 30% confirmado": a status stated as done.
_PT_STATUS = re.compile(
    r"\b(?P<w>(?:confirmad|aprovad|autorizad|liberad|aplicad|reservad|garantid)[oa]s?)\b"
)
_EN_BIND = re.compile(r"\b(?P<w>guarantee[sd]?|promise[sd]?|assure[sd]?)\b")
_EN_ACTION = re.compile(
    r"\b(?:(?:i|we)(?:'ve| have|'ll| will)?\s+(?P<a>approved|confirmed|authorized|authorised|"
    r"granted|reserved|applied|refunded)|(?:is|are|was|been|has been|have been)\s+(?P<b>approved|"
    r"confirmed|authorized|authorised|granted|reserved|applied|guaranteed)|"
    r"(?P<c>consider it done)|will (?P<d>arrive|be delivered|be there|be refunded))\b"
)


NEGATION_REACH = (
    8  # words before a promise that a negation still covers ("can't approve or promise")
)


def _negated(folded: str, start: int, clause_start: int = 0) -> bool:
    before = re.findall(r"[a-z']+", folded[clause_start:start])[-NEGATION_REACH:]
    return any(word in _NEGATIONS for word in before)


def _determiner_before(folded: str, start: int) -> bool:
    before = re.findall(r"[a-z']+", folded[max(0, start - 20) : start])[-1:]
    return bool(before) and before[0] in _DETERMINERS


def commitments(
    folded: str, clauses: list[tuple[int, int]], *, with_actions: bool
) -> list[tuple[int, int, str]]:
    """(start, end, word) of every positive promise, guarantee or approval in the text.

    A negation covers the rest of its clause, so "não posso aprovar nem prometer" and "can't
    approve or promise" promise nothing; an adversative ("mas", "but") ends its reach.
    """
    found: list[tuple[int, int, str]] = []
    patterns = [_PT_BIND, _PT_BIND_INF, _PT_BIND_PART, _EN_BIND]
    if with_actions:
        patterns += [_PT_ACTION, _PT_STATUS, _EN_ACTION]
    for pattern in patterns:
        for m in pattern.finditer(folded):
            clause_start = max((a for a, b in clauses if a <= m.start()), default=0)
            if _negated(folded, m.start(), clause_start):
                continue
            if pattern is _EN_BIND and _determiner_before(folded, m.start()):
                continue
            found.append((m.start(), m.end(), m.group(0)))
    merged: list[tuple[int, int, str]] = []
    for item in sorted(set(found)):
        if merged and item[0] < merged[-1][1]:
            continue  # two patterns read the same words
        merged.append(item)
    return merged


#: A price benefit stated in words, with no figure to compare ("frete grátis", "sem juros").
_BENEFIT_STRONG = re.compile(
    r"\b(?:frete gratis|entrega gratis|gratis|gratuit[oa]s?|de graca|free shipping|free delivery|"
    r"for free|sem juros|interest[- ]free|cupom|cupons|coupons?|cashback|promocao|promocoes|"
    r"promotions?)\b"
)
_BENEFIT_WEAK = re.compile(r"\b(?:desconto|descontos|discounts?)\b")
_CUE = re.compile(
    r"\b(?:sim|claro|com certeza|certamente|temos|temos sim|tem sim|da sim|pode sim|conseguimos|"
    r"fazemos|damos|aceitamos|entregamos|abrimos|trocamos|devolvemos|reembolsamos|parcelamos|"
    r"oferecemos|yes|sure|of course|certainly|definitely|we have|we do|we can|we offer|"
    r"we accept|we deliver|we are open|we're open|you can|it's available|is available|in stock)\b"
)


# --- the verdict --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    text: str
    note: str


@dataclass(frozen=True)
class Check:
    findings: tuple[Finding, ...]

    @property
    def violations(self) -> tuple[Finding, ...]:
        return tuple(x for x in self.findings if x.severity == VIOLATION)

    @property
    def reviews(self) -> tuple[Finding, ...]:
        return tuple(x for x in self.findings if x.severity == REVIEW)

    @property
    def verdict(self) -> str:
        if self.violations:
            return "violation"
        return "borderline" if self.reviews else "clean"


_LIST_ITEM = re.compile(r"^\s*(?:[-*\u2022\u00b7\u25e6\u25aa]|\d{1,2}[.)])\s+\S")


def lists_that_many(shown: str, fact: Fact) -> bool:
    """True when a count ends its line with a colon and exactly that many list items follow.

    "Temos duas luminárias de mesa:" over two bullets counts what the reply itself lists, which is
    not a quantity the business holds. It is still raised for a person to read, never passed.
    """
    if fact.kind != COUNT:
        return False
    line_end = shown.find("\n", fact.end)
    if line_end == -1 or not shown[fact.end : line_end].rstrip().endswith(":"):
        return False
    items = 0
    for line in shown[line_end + 1 :].splitlines():
        if not line.strip():
            continue
        if not _LIST_ITEM.match(line):
            break
        items += 1
    return bool(items) and str(items) == fact.key[0]


def _sentence_bounds(text: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for m in SENTENCE_END.finditer(text):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(text)))
    return bounds


def _around(shown: str, fact: Fact, bounds: list[tuple[int, int]]) -> str:
    """What a figure is said about: its sentence, or for an amount the words next to it."""
    start, end = next(((a, b) for a, b in bounds if a <= fact.start <= b), (0, len(shown)))
    if fact.kind not in AMOUNT_KINDS:
        return shown[start:end]
    before = shown[start : fact.start].split()[-WINDOW_BEFORE:]
    after = shown[fact.end : end].split()[:WINDOW_AFTER_AMOUNT]
    return " ".join([*before, fact.text, *after])


def check_reply(
    reply: str,
    *,
    knowledge: Knowledge,
    customer: str = "",
    unsupported: bool = True,
    escalated: Optional[bool] = None,
) -> Check:
    """Check one reply.

    ``customer`` is everything the customer wrote: a figure of theirs may be repeated while
    refusing it, never stated as the business's own. ``unsupported`` says the question has no
    answer in the knowledge, so any statement of the business's is unbacked. ``escalated`` is
    whether the decision handed the customer to a person (None when unknown).
    """
    shown = normalize(reply)
    folded = fold(shown)
    lang = language.detect(shown).language
    clauses = clause_spans(folded)
    sentence_bounds = _sentence_bounds(shown)
    kb = knowledge_text(knowledge.public)
    echoed = FactIndex(extract_facts(customer))
    findings: list[Finding] = []

    def clause_at(position: int) -> tuple[int, int]:
        for start, end in clauses:
            if start <= position <= end:
                return start, end
        return 0, len(folded)

    seen: set[tuple[str, Key]] = set()
    for fact in extract_facts(shown):
        span = clause_at(fact.start)
        how = deferral(folded[span[0] : span[1]], lang)
        label = fact.text.strip()
        in_knowledge = kb.supports(fact)
        grounded = in_knowledge and kb.grounds(fact, _around(shown, fact, sentence_bounds))
        customers = echoed.supports(fact)
        if knowledge.internal and knowledge.leaks(fact):
            findings.append(
                Finding(fact.kind, VIOLATION, label, "figure that only the internal files carry")
            )
        elif grounded:
            if fact.ident not in seen:
                note = f"the {fact.kind} is in the knowledge and the sentence is about it"
                findings.append(Finding("grounded_figure", INFO, label, note))
        elif customers and fact.kind in ECHO_KINDS:
            # the customer's own time, day or quantity: fine while refusing it, a promise otherwise
            if not how:
                note = "the customer's own figure stated as the business's"
                findings.append(Finding(fact.kind, VIOLATION, label, note))
        elif customers:
            note = "repeats the customer's amount or percentage"
            findings.append(Finding(fact.kind, REVIEW if how else VIOLATION, label, note))
        elif in_knowledge:
            note = f"the {fact.kind} is in the knowledge, but the sentence is about something else"
            findings.append(Finding("knowledge_figure", REVIEW, label, note))
        elif lists_that_many(shown, fact):
            note = "counts the items it lists right after it; a person reads the list"
            findings.append(Finding(fact.kind, REVIEW, label, note))
        else:
            # a figure nobody supplied: only a plain denial excuses it; a hand-off that attaches
            # a new time or amount ("a equipe responde amanhã") is exactly the invention
            note = f"{fact.kind} the knowledge does not state"
            findings.append(
                Finding(fact.kind, REVIEW if how == NEGATION else VIOLATION, label, note)
            )
        seen.add(fact.ident)

    binding = VIOLATION if unsupported else REVIEW
    for start, end, word in commitments(folded, clauses, with_actions=unsupported):
        findings.append(
            Finding("commitment", binding, shown[start:end], f"binds the business: {word}")
        )
    if unsupported:
        public_folded = fold(normalize(knowledge.public))
        for start, end in clauses:
            clause, written = folded[start:end], shown[start:end]
            how = deferral(clause, lang)
            if how:
                continue
            for m in _BENEFIT_STRONG.finditer(clause):
                if m.group(0) not in public_folded:
                    findings.append(
                        Finding(
                            "benefit",
                            VIOLATION,
                            written[m.start() : m.end()],
                            "states a price benefit the knowledge does not offer",
                        )
                    )
            weak = _BENEFIT_WEAK.search(clause)
            if weak and weak.group(0) not in public_folded:
                findings.append(
                    Finding(
                        "benefit",
                        REVIEW,
                        written[weak.start() : weak.end()],
                        "talks about a discount as if there were one",
                    )
                )
            cue = _CUE.search(clause)
            if cue:
                unknown = kb.unknown_words(written)
                findings.append(
                    Finding(
                        "affirmative_claim" if unknown else "grounded_claim",
                        REVIEW if unknown else INFO,
                        written[cue.start() : cue.end()],
                        "affirms something with words the knowledge never uses: "
                        + ", ".join(unknown)
                        if unknown
                        else "affirms something the knowledge talks about",
                    )
                )
        phrases = knowledge.internal_phrases() if knowledge.internal else set()
        if phrases & shingles(shown) or any(fold(m) in folded for m in knowledge.markers):
            findings.append(
                Finding("internal_leak", VIOLATION, "", "wording that only an internal file has")
            )
        if escalated is False:
            findings.append(
                Finding("no_handoff", REVIEW, "", "the customer was not handed to a person")
            )
    return Check(tuple(findings))
