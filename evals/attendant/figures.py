"""The figures a text states: amounts, percentages, dates, times, durations and the like.

Each figure is read out with a value that compares across notations ("R$ 49", "49 reais" and
"quarenta e nove reais" are the same amount; "9h", "09:00" and "9 am" the same time), so a reply's
figures can be held against the knowledge text mechanically. Portuguese and English.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

MONEY = "money"
PERCENT = "percent"
DATE = "date"
TIME = "time"
DURATION = "duration"
MEASURE = "measure"
COUNT = "count"
INSTALLMENTS = "installments"
WEEKDAY = "weekday"
MONTH = "month"
RELATIVE = "relative_day"
PERIOD = "part_of_day"

#: A reply that refuses or defers may repeat these from the customer; an amount never counts.
ECHO_KINDS = frozenset(
    {DATE, TIME, DURATION, MEASURE, COUNT, INSTALLMENTS, WEEKDAY, MONTH, RELATIVE, PERIOD}
)

Key = tuple[object, ...]
Adder = Callable[[str, Key, int, int], None]


# --- text ---------------------------------------------------------------------------------


def normalize(text: str) -> str:
    """What a reader sees: compatibility forms folded, invisible characters gone, dashes plain."""
    out = []
    for ch in unicodedata.normalize("NFKC", text.replace("’", "'")):
        category = unicodedata.category(ch)
        if category == "Cf":
            continue
        if category == "Pd" or ch == "−":
            out.append("-")
        elif category in ("Zs", "Zl", "Zp") or ch in "\t\r":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def fold(text: str) -> str:
    """Lower case without accents, one character for one, so positions line up with the text."""
    out = []
    for ch in text:
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        low = base.lower()
        out.append(low if len(low) == 1 else ch)
    return "".join(out)


# --- numbers ------------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?"


def parse_number(raw: str) -> Optional[Decimal]:
    """``1.200,50`` and ``1,200.50`` are 1200.5; ``189,00`` is 189; ``1.500`` is 1500."""
    text = raw.strip()
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = text.split(sep)
        if len(parts) > 2:
            text = "".join(parts)
        elif len(parts[1]) == 3 and 1 <= len(parts[0]) <= 3 and parts[0] != "0":
            text = parts[0] + parts[1]
        else:
            text = parts[0] + "." + parts[1]
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def number_key(value: Decimal) -> str:
    return format(value.normalize(), "f")


_ONES = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13,
    "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17,
    "dezoito": 18, "dezenove": 19,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70,
    "oitenta": 80, "noventa": 90, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_HUNDREDS = {
    "cem": 100, "cento": 100, "duzentos": 200, "duzentas": 200, "trezentos": 300,
    "quatrocentos": 400, "quinhentos": 500, "seiscentos": 600, "setecentos": 700,
    "oitocentos": 800, "novecentos": 900,
}  # fmt: skip
_SCALES = {"mil": 1000, "thousand": 1000}
_ARTICLE_ONES = frozenset({"um", "uma", "one"})
_WORD_TOKEN = re.compile(r"[a-z]+")
_JOIN = re.compile(r"^[\s-]+$")


def _word_kind(word: str) -> str:
    if word in _ONES:
        return "ones"
    if word in _TENS:
        return "tens"
    if word in _HUNDREDS or word == "hundred":
        return "hundreds"
    if word in _SCALES:
        return "scale"
    return ""


def word_numbers(folded: str) -> list[tuple[int, int, Decimal]]:
    """Runs of number words ("três", "vinte e cinco", "two hundred") with their value.

    A lone "um", "uma" or "one" is an article, not a quantity, and never starts a run.
    """
    tokens = [(m.group(0), m.start(), m.end()) for m in _WORD_TOKEN.finditer(folded)]
    runs: list[tuple[int, int, Decimal]] = []
    i, n = 0, len(tokens)
    while i < n:
        word = tokens[i][0]
        starts_with_hundred = i + 1 < n and tokens[i + 1][0] in ("hundred", "thousand", "mil")
        if not _word_kind(word) or (word in _ARTICLE_ONES and not starts_with_hundred):
            i += 1
            continue
        total = current = 0
        last = i
        prev_kind = ""
        j = i
        while j < n:
            w, s, _ = tokens[j]
            if j > i and not _JOIN.match(folded[tokens[j - 1][2] : s]):
                break
            kind = _word_kind(w)
            if kind == "ones":
                current += _ONES[w]
            elif kind == "tens":
                current += _TENS[w]
            elif kind == "hundreds":
                current = (current or 1) * 100 if w == "hundred" else current + _HUNDREDS[w]
            elif kind == "scale":
                total += (current or 1) * _SCALES[w]
                current = 0
            elif (
                w in ("e", "and")
                and prev_kind in ("tens", "hundreds", "scale")
                and j + 1 < n
                and _word_kind(tokens[j + 1][0])
                and _JOIN.match(folded[tokens[j][2] : tokens[j + 1][1]])
            ):
                j += 1
                continue
            else:
                break
            prev_kind = kind
            last = j
            j += 1
        runs.append((tokens[i][1], tokens[last][2], Decimal(total + current)))
        i = last + 1
    return runs


# --- facts --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    kind: str
    key: Key
    text: str
    start: int
    end: int

    @property
    def ident(self) -> tuple[str, Key]:
        return (self.kind, self.key)


_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip
_MONTH_ABBR = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "mai": 5, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "aug": 8, "set": 9, "sept": 9, "sep": 9, "out": 10, "oct": 10,
    "nov": 11, "dez": 12, "dec": 12,
}  # fmt: skip
_MONTH_ALL = {**_MONTHS, **_MONTH_ABBR}
_MONTH_ANY = "|".join(sorted(_MONTH_ALL, key=len, reverse=True))
_MONTH_FULL = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAYS = {
    "segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5,
    "sunday": 6,
}  # fmt: skip
_ORDINAL_WEEKDAYS = frozenset({"segunda", "terca", "quarta", "quinta", "sexta"})
#: "segunda via", "quarta parte": the ordinal, not the day.
_ORDINAL_NOUNS = frozenset(
    """via opcao vez parte etapa chance mensagem pessoa unidade mao tentativa coluna linha fase
    rodada pergunta resposta camada leitura edicao""".split()
)

_CURRENCY = (
    r"reais|real|dolares?|dolar|euros?|usd|brl|eur|dollars?|bucks"
)  # fmt: skip
_UNIT = (
    r"(?P<u>dias?(?:\s+(?:uteis|util|corridos?))?|(?:business\s+|working\s+|calendar\s+)?days?"
    r"|horas?|hours?|hrs?|semanas?|weeks?|mes(?:es)?|months?|minutos?|minutes?|mins?|anos?|years?)"
)
_MEASURE_UNITS = {
    "cm": "cm", "mm": "mm", "ml": "ml", "kg": "kg", "g": "g", "l": "l", "w": "w", "v": "v",
    "m": "m", "litro": "l", "litros": "l", "metro": "m", "metros": "m", "grama": "g",
    "gramas": "g", "watt": "w", "watts": "w", "volt": "v", "volts": "v", "polegada": "in",
    "polegadas": "in", "kw": "kw", "kwh": "kwh",
}  # fmt: skip
_MEASURE_RE = "|".join(sorted(_MEASURE_UNITS, key=len, reverse=True))

_PERCENT = re.compile(rf"(?<![\w.,])(?P<n>{_NUM})\s*(?:%|por\s?cento\b|percent\b|per\s?cent\b)")
_MONEY_SYMBOL = re.compile(rf"(?<![\w$])(?:r\$|us\$|u\$s|\$|€|£)\s*(?P<n>{_NUM})")
_MONEY_WORD = re.compile(rf"(?<![\w.,])(?P<n>{_NUM})\s*(?P<k>mil\s+)?(?:{_CURRENCY})\b")
_ISO_DATE = re.compile(r"(?<!\d)(?P<y>20\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?!\d)")
_DMY_SLASH = re.compile(r"(?<![\d/.,-])(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{2,4}))?(?![\d/])")
_DMY_DOTTED = re.compile(
    r"(?<![\d/.,-])(?P<d>\d{1,2})[.-](?P<m>\d{1,2})[.-](?P<y>\d{2,4})(?![\d/])"
)
_DATE_PT = re.compile(
    rf"(?<![\d/.,-])(?P<d>\d{{1,2}})\s*(?:o\s*)?(?:de\s+)?(?P<mo>{_MONTH_ANY})\b\.?(?!\s+of\b)(?:\s+de\s+\d{{4}})?"
)
_DATE_EN_MONTH_FIRST = re.compile(
    rf"\b(?P<mo>{_MONTH_ANY})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*[:h])(?:,?\s+\d{{4}})?"
)
_DATE_EN_DAY_FIRST = re.compile(
    rf"(?<![\d/.,-])(?P<d>\d{{1,2}})(?:st|nd|rd|th)\s+(?:of\s+)?(?P<mo>{_MONTH_ANY})\b(?:,?\s+\d{{4}})?"
)
_DAY_OF_MONTH = re.compile(r"\bdia\s+(?P<d>\d{1,2})\b(?!\s*(?:/|de\s+\w))")
_INSTALL_X = re.compile(r"(?<![\w.,])(?P<n>\d{1,2})\s?[x×](?![\w])")
_INSTALL_WORD = re.compile(
    r"(?<![\w.,])(?P<n>\d{1,2})\s*(?:vezes|parcelas?|prestacoes|installments?)\b"
)
_INSTALL_IN = re.compile(
    r"\b(?:parcel\w*|dividir\w*|split\w*)\s+(?:em|de|in|into)\s+(?P<n>\d{1,2})\b"
)
_TIME_COLON = re.compile(
    r"(?<![\d:.,/])(?P<h>\d{1,2}):(?P<m>\d{2})(?!\d)(?:\s*(?P<ap>am|pm|a\.m\.|p\.m\.))?"
)
_TIME_AMPM = re.compile(
    r"(?<![\d:.,/])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s?(?P<ap>am|pm|a\.m\.|p\.m\.)(?![a-z])"
)
_TIME_H = re.compile(r"(?<![\d:.,/])(?P<h>\d{1,2})\s?hs?(?:\s?(?P<m>\d{2}))?(?![a-z0-9])")
_TIME_HORAS = re.compile(
    r"\b(?:as|das|ate as|ate|a partir das|at|by|until|from|before|after|around)\s+"
    r"(?P<h>\d{1,2})\s*(?:horas?|hrs?|hours?)\b"
)
_NOON = re.compile(r"\b(?:meio[- ]dia|noon)\b")
_MIDNIGHT = re.compile(r"\b(?:meia[- ]noite|midnight)\b")
_DURATION = re.compile(rf"(?<![\w.,])(?P<n>{_NUM})(?:\s*|-)(?:de\s+)?{_UNIT}\b")
_ARTICLE_DURATION = re.compile(rf"\b(?P<n>um|uma|one)\s+{_UNIT}\b")
_MEASURE = re.compile(rf"(?<![\w.,])(?P<n>{_NUM})\s*(?P<u>{_MEASURE_RE})\b")
_COUNT = re.compile(r"(?<![\w.,/:-])(?P<n>\d+(?:[.,]\d+)?)(?![\w])")
_PHONE = re.compile(r"\+?\d(?:[\s().-]?\d){7,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+")
_LIST_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s")
_WEEKDAY_RE = re.compile(rf"\b(?P<w>{'|'.join(_WEEKDAYS)})(?P<f>-?\s?feira)?s?\b")
_MONTH_RE = re.compile(rf"\b(?P<mo>{_MONTH_FULL})\b")
_RELATIVE_RE = re.compile(
    r"\b(?P<r>hoje mesmo|hoje|amanha|ontem|depois de amanha|esta semana|essa semana|nesta semana"
    r"|nessa semana|semana que vem|proxima semana|proximo mes|mes que vem|fim de semana"
    r"|final de semana|fins de semana|today|tomorrow|tonight|yesterday|next week|this week"
    r"|next month|this weekend|the weekend|this morning|this afternoon|this evening)\b"
)
_PERIOD_RE = re.compile(
    r"\b(?:(?:de|da|na|pela|a|as|esta|nesta|essa|nessa|durante a|ao)\s+(?P<p>manha|tarde|noite"
    r"|madrugada)|in the (?P<e>morning|afternoon|evening)|(?P<n>at night|overnight))\b"
)
#: Phrases where "today" or "hoje" is a courtesy and states nothing about a date.
_BENIGN_RELATIVE = re.compile(
    r"\b(?:how (?:can|may) i (?:help|assist)[^.!?]*|em que posso (?:te |lhe )?ajudar"
    r"|como posso (?:te |lhe )?ajudar)\s*(?:you\s+)?(?:today|hoje)\b"
)


def _unit_family(unit: str) -> str:
    if unit.startswith(("dia", "day", "business", "working", "calendar")):
        return "day"
    if unit.startswith(("hora", "hour", "hr")):
        return "hour"
    if unit.startswith(("seman", "week")):
        return "week"
    if unit.startswith(("mes", "month")):
        return "month"
    if unit.startswith(("minut", "min")):
        return "minute"
    return "year"


def _number(match: re.Match[str], group: str = "n") -> Optional[Decimal]:
    return parse_number(match.group(group))


class _Spans:
    """Where facts were already found, so the same digits are not read twice."""

    def __init__(self) -> None:
        self._taken: list[tuple[int, int]] = []

    def free(self, start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in self._taken)

    def take(self, start: int, end: int) -> None:
        self._taken.append((start, end))


def extract_facts(text: str) -> list[Fact]:
    """Every figure the text states, in the order it appears. One flat list of passes."""
    shown = normalize(text)
    f = fold(shown)
    spans = _Spans()
    facts: list[Fact] = []

    def add(kind: str, key: Key, start: int, end: int) -> None:
        if spans.free(start, end):
            spans.take(start, end)
            facts.append(Fact(kind, key, shown[start:end], start, end))

    for pattern in (_EMAIL, _URL, _LIST_MARKER):
        for m in pattern.finditer(f):
            spans.take(m.start(), m.end())
    for m in re.finditer(r"(?<![\d/])24/7(?![\d/])", f):
        spans.take(m.start(), m.end())

    for m in _PERCENT.finditer(f):
        value = _number(m)
        if value is not None:
            add(PERCENT, (number_key(value),), m.start(), m.end())
    for m in _MONEY_SYMBOL.finditer(f):
        value = _number(m)
        if value is not None:
            add(MONEY, (number_key(value),), m.start(), m.end())
    for m in _MONEY_WORD.finditer(f):
        value = _number(m)
        if value is not None:
            value = value * (1000 if m.group("k") else 1)
            add(MONEY, (number_key(value),), m.start(), m.end())

    for m in _ISO_DATE.finditer(f):
        d, mo = int(m.group("d")), int(m.group("m"))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            add(DATE, (d, mo), m.start(), m.end())
    for pattern in (_DMY_SLASH, _DMY_DOTTED):
        for m in pattern.finditer(f):
            d, mo = int(m.group("d")), int(m.group("m"))
            if 1 <= d <= 31 and 1 <= mo <= 12:
                add(DATE, (d, mo), m.start(), m.end())
    for pattern in (_DATE_PT, _DATE_EN_DAY_FIRST, _DATE_EN_MONTH_FIRST):
        for m in pattern.finditer(f):
            d = int(m.group("d"))
            if 1 <= d <= 31:
                add(DATE, (d, _MONTH_ALL[m.group("mo")]), m.start(), m.end())
    for m in _DAY_OF_MONTH.finditer(f):
        d = int(m.group("d"))
        if 1 <= d <= 31:
            add(DATE, (d, None), m.start(), m.end())

    for pattern in (_INSTALL_X, _INSTALL_WORD, _INSTALL_IN):
        for m in pattern.finditer(f):
            add(INSTALLMENTS, (int(m.group("n")),), m.start(), m.end())

    for m in _TIME_COLON.finditer(f):
        _add_time(add, m, m.start(), m.end())
    for m in _TIME_AMPM.finditer(f):
        _add_time(add, m, m.start(), m.end())
    for m in _TIME_HORAS.finditer(f):
        _add_time(add, m, m.start("h"), m.end())
    for m in _TIME_H.finditer(f):
        hour = int(m.group("h"))
        if hour > 23:
            add(DURATION, (number_key(Decimal(hour)), "hour"), m.start(), m.end())
        else:
            _add_time(add, m, m.start(), m.end())
    for pattern, key in ((_NOON, (12, 0)), (_MIDNIGHT, (0, 0))):
        for m in pattern.finditer(f):
            add(TIME, key, m.start(), m.end())

    for m in _DURATION.finditer(f):
        value = _number(m)
        if value is not None:
            add(DURATION, (number_key(value), _unit_family(m.group("u"))), m.start(), m.end())
    for m in _ARTICLE_DURATION.finditer(f):
        add(DURATION, ("1", _unit_family(m.group("u"))), m.start(), m.end())
    for m in _MEASURE.finditer(f):
        value = _number(m)
        if value is not None:
            add(MEASURE, (number_key(value), _MEASURE_UNITS[m.group("u")]), m.start(), m.end())

    for start, end, value in word_numbers(f):
        _add_word_number(add, f, start, end, value)

    for m in _MONTH_RE.finditer(f):
        add(MONTH, (_MONTHS[m.group("mo")],), m.start(), m.end())
    for m in _WEEKDAY_RE.finditer(f):
        word = m.group("w")
        if word in _ORDINAL_WEEKDAYS and not m.group("f"):
            after = f[m.end() :].split()[:1] or [""]
            if after[0].strip(".,!?") in _ORDINAL_NOUNS:
                continue
        add(WEEKDAY, (_WEEKDAYS[word],), m.start(), m.end())
    for m in _RELATIVE_RE.finditer(f):
        if _BENIGN_RELATIVE.search(f[max(0, m.start() - 60) : m.end()]):
            continue
        add(RELATIVE, (m.group("r"),), m.start(), m.end())
    for m in _PERIOD_RE.finditer(f):
        period = m.group("p") or m.group("e") or m.group("n") or ""
        add(PERIOD, (period,), m.start(), m.end())

    for m in _PHONE.finditer(f):
        if len(re.sub(r"\D", "", m.group(0))) >= 8:
            spans.take(m.start(), m.end())
    for m in _COUNT.finditer(f):
        digits = re.sub(r"\D", "", m.group("n"))
        if len(digits) >= 8:
            continue  # a phone number or an identifier, the egress check's business
        value = _number(m)
        if value is not None:
            add(COUNT, (number_key(value),), m.start(), m.end())
    return sorted(facts, key=lambda fact: fact.start)


def _add_time(add: Adder, m: re.Match[str], start: int, end: int) -> None:
    hour = int(m.group("h"))
    minute = int(m.groupdict().get("m") or 0)
    marker = (m.groupdict().get("ap") or "").replace(".", "")
    if marker == "pm" and hour < 12:
        hour += 12
    elif marker == "am" and hour == 12:
        hour = 0
    if hour <= 24 and minute <= 59:
        add(TIME, (hour % 24, minute), start, end)


_AFTER_WORD = re.compile(r"[\s-]*(?:de\s+)?([a-z]+(?:\s+[a-z]+)?)")


def _add_word_number(add: Adder, folded: str, start: int, end: int, value: Decimal) -> None:
    tail = _AFTER_WORD.match(folded, end)
    following = tail.group(1).split() if tail else []
    first = following[0] if following else ""
    key = number_key(value)
    if first in ("real", "reais", "dolar", "dolares", "euro", "euros", "dollar", "dollars", "usd"):
        add(MONEY, (key,), start, tail.end() if tail else end)
    elif first == "por" and following[1:2] == ["cento"] or first in ("percent", "per"):
        add(PERCENT, (key,), start, tail.end() if tail else end)
    elif re.fullmatch(_UNIT.replace("(?P<u>", "(?:"), first):
        add(DURATION, (key, _unit_family(first)), start, tail.end() if tail else end)
    elif first in _MEASURE_UNITS:
        add(MEASURE, (key, _MEASURE_UNITS[first]), start, tail.end() if tail else end)
    elif first in ("vezes", "parcelas", "parcela"):
        add(INSTALLMENTS, (int(value),), start, tail.end() if tail else end)
    else:
        add(COUNT, (key,), start, end)


# --- comparing ----------------------------------------------------------------------------


def date_matches(a: Key, b: Key) -> bool:
    return a[0] == b[0] and (a[1] is None or b[1] is None or a[1] == b[1])


class FactIndex:
    """The figures a text states, by kind."""

    def __init__(self, facts: Iterable[Fact]) -> None:
        self._keys: dict[str, set[Key]] = {}
        for fact in facts:
            self._keys.setdefault(fact.kind, set()).add(fact.key)

    def supports(self, fact: Fact) -> bool:
        keys = self._keys.get(fact.kind, set())
        if fact.kind == DATE:
            return any(date_matches(fact.key, key) for key in keys)
        return fact.key in keys
