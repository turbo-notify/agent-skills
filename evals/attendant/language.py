"""Which language a short reply is written in: Portuguese, English, or not clear.

A plain count of common words, with no model involved, so the answer is the same on every run.
Words that both languages use ("a", "no", "as", "me") are left out of the lists on purpose.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PT = "pt"
EN = "en"
UNKNOWN = "unknown"

_PT_WORDS = frozenset(
    """de da dos das que nao para pra com uma um os voce voces em por mais ao aos na nos nas ou
    sao foi ser tem temos tenho nossa nosso nossos nossas sua seu suas seus aqui tambem ja como
    qual quais quando sobre vou vamos pode posso podemos obrigado obrigada ola oi bom boa dia tarde
    noite loja entrega entregamos pedido preco valor horario sabado segunda sexta domingo vela
    luminaria estoque equipe pessoa alguem ajudar ajuda informacao confirmar verificar isso essa
    esse esta este meu minha muito claro sim aceitamos dias uteis horas reais atendimento abrimos
    fechado funciona troca devolucao prazo frete endereco depois antes ate se mas porque estou
    estamos fica ficar quer queria gostaria desculpe desculpa""".split()
)
_EN_WORDS = frozenset(
    """the is are you your to of and for with we our can will this that have has it in on at be not
    if does from please thanks thank hello hi sorry store shop delivery order price hours open
    closed saturday monday friday sunday how what when where which who about would could should may
    also just here there some any need help let know check team someone person one two get back
    soon an or by so than then they them their its was were been am my i free right again
    unfortunately sure glad welcome""".split()
)
_WORD = re.compile(r"[a-zA-ZÀ-ÿ']+")
_PT_ACCENTS = frozenset("ãõçáéíóúâêôà")


@dataclass(frozen=True)
class Detected:
    language: str
    pt_hits: int
    en_hits: int


def _fold(word: str) -> str:
    base = unicodedata.normalize("NFKD", word.lower())
    return "".join(ch for ch in base if not unicodedata.combining(ch))


def detect(text: str) -> Detected:
    """The language with clearly more common words; accents typical of Portuguese count too."""
    pt = en = 0
    for word in _WORD.findall(text):
        folded = _fold(word)
        pt += folded in _PT_WORDS
        en += folded in _EN_WORDS
    pt += min(sum(1 for ch in text.lower() if ch in _PT_ACCENTS), 3)
    if pt > en:
        return Detected(PT, pt, en)
    if en > pt:
        return Detected(EN, pt, en)
    return Detected(UNKNOWN, pt, en)
