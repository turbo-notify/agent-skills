"""The platform's customer-copy guards, mirrored for this module's texts.

Sources: the platform customer-copy voice standard and the `no AI-sounding phrasing`
and `no internal vocabulary` tests of `public-docs-web/tests/source/copy.test.mjs`. Keep equal.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

AI_SOUNDING = re.compile(
    r"ningu[eé]m por perto|vale em poucos minutos|nada (para|a) (instalar|fazer aqui)|"
    r"nothing (else )?to install|(nobody|no one) (is )?around|sem complica[çc][õo]es|"
    r"sem pegadinhas?|é simples assim|it'?s that simple|hassle[- ]free|\bseamless(ly)?\b|"
    r"\bleverag(e|es|ing)\b|\bunlock(s|ing)?\b|\bstreamlin(e|es|ing)\b|game[- ]chang|"
    r"cutting[- ]edge|world[- ]class|best[- ]in[- ]class|\brobust[oa]s?\b|\bpoderos[oa]s?\b|"
    r"sem fric[çc][ãa]o|\bfrictionless\b|\bDescubra\b|nenhuma a[çc][ãa]o [ée] necess|"
    r"no action (is )?(needed|required)|pronta? para ajudar você|ready to help you",
    re.IGNORECASE,
)
INTERNAL_VOCABULARY = re.compile(
    r"\b(device[- ]links?|tenants?|multi-tenant|workers?|NATS|JetStream|upstream|ADR|neonize|"
    r"whatsmeow|stdio)\b|neste ambiente|this environment",
    re.IGNORECASE,
)
DASH = re.compile("[—–]")
PT_ENGLISH_WORDS = re.compile(
    r"\b(handle|inbox|chars|default|trial|fan-out|quotas?)\b", re.IGNORECASE
)
EN_PORTUGUESE_WORDS = re.compile(r"\bcotas?\b", re.IGNORECASE)


def violations(text: str, *, lang: str | None = None) -> list[str]:
    found: list[str] = []
    for name, pattern in (
        ("ai-sounding", AI_SOUNDING),
        ("internal vocabulary", INTERNAL_VOCABULARY),
        ("dash", DASH),
    ):
        if pattern.search(text):
            found.append(name)
    if lang == "pt" and PT_ENGLISH_WORDS.search(text):
        found.append("english word in pt")
    if lang == "en" and EN_PORTUGUESE_WORDS.search(text):
        found.append("portuguese word in en")
    return found


def scan(texts: Iterable[tuple[str, str, str]]) -> list[str]:
    """`(where, lang, text)` triples; returns `where: problem` lines."""
    return [f"{where}: {v}" for where, lang, text in texts for v in violations(text, lang=lang)]
