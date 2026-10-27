"""Does a sentence of the reply stand on the knowledge, or only borrow a figure from it?

A reply to a question the knowledge cannot answer may still quote a price or an hour the knowledge
does state ("Só para referência, a Aurora custa R$ 189,00"). That is fine when the sentence is about
the thing the figure belongs to, and a misapplied figure when it is not ("vence em 3 dias" with the
delivery time). The test is the subject: the sentence must share a distinctive word with a sentence
of the knowledge that states the same figure.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

from evals.attendant.figures import (
    DATE,
    Fact,
    FactIndex,
    Key,
    date_matches,
    extract_facts,
    fold,
    normalize,
)

#: A word that appears in at most this many knowledge sentences names a subject.
DISTINCTIVE = 4

#: Words that carry no subject: function words, conversation filler, and the verbs and nouns of
#: asking, telling and pricing. A sentence is "about" its other words.
_STOP = frozenset(
    """
    para pelo pela pelos pelas como mais muito muita muitos muitas tudo todo toda todos todas isso
    isto esse essa esses essas este esta estes estas aquele aquela nosso nossa nossos nossas seu
    sua seus suas meu minha voce voces ainda tambem quando onde qual quais porque porem entao
    agora aqui assim sobre entre depois antes desde sempre nunca apenas somente mesmo outro outra
    outros outras algum alguma alguns algumas nenhum nenhuma cada ficou fica ficam vamos vai
    podemos posso pode podem tenho temos tinha pedir pedido passar passo passa informacao
    informacoes equipe pessoa alguem continua continuar conversa responde responder retorna
    retornar confirmar verificar confirma enquanto claro obrigado obrigada ola boa bom dia tarde
    noite bem certo certeza dessa desse nessa nesse seria foram estou estamos quer queria quero
    gostaria desculpe desculpas custa custam sai saem valor valores preco precos total quiser
    olhada olhar dar dando tiver tenha possa possivel disponivel aviso aviso avisar atendimento
    entrar contato falar vamos aguardar aguarde breve momento hoje amanha apos esteja estiver
    chega chegar chegou normalmente geralmente especial existe opcao opcoes parte
    that this these those with from have has will would could should might your you our the and
    for are was were been being their them they there here what when where which whom whose about
    into over also just than then very much more most some such only other another each every both
    either neither none always never still even again once already soon shortly please thanks
    thank hello help know check confirm continue follow price cost costs value
    dias hora horas semana semanas meses minutos anos reais real days hours week weeks
    month months minutes years unidade unidades units
    """.split()
)
_WORDS = re.compile(r"[a-z]{4,}")
#: A label and what it labels ("Aurora (...): R$ 189,00") stay together: no split at a colon.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def stems(text: str) -> frozenset[str]:
    """The subject words of a text: folded, without filler, cut to five letters."""
    return frozenset(w[:5] for w in _WORDS.findall(fold(normalize(text))) if w not in _STOP)


def sentences(text: str) -> list[str]:
    parts = (p.strip(" -*#•") for p in SENTENCE_END.split(normalize(text)))
    return [p for p in parts if p]


class KnowledgeText:
    """A knowledge text split in sentences, each with its figures and its subject words."""

    def __init__(self, text: str) -> None:
        rows = [(stems(s), extract_facts(s)) for s in sentences(text)]
        self.facts = FactIndex(fact for _, found in rows for fact in found)
        self.vocabulary: frozenset[str] = frozenset().union(*(words for words, _ in rows))
        self._frequency = Counter(word for words, _ in rows for word in words)
        self._rows: list[tuple[frozenset[str], frozenset[tuple[str, Key]]]] = [
            (words, frozenset(fact.ident for fact in found)) for words, found in rows
        ]

    def supports(self, fact: Fact) -> bool:
        return self.facts.supports(fact)

    def grounds(self, fact: Fact, sentence: str) -> bool:
        """True when ``sentence`` is about what a sentence of the knowledge states ``fact`` of.

        The figure's own words prove nothing ("sexta" is in the opening hours and in a promise to
        deliver on Friday), so the link has to be another subject word, or a figure of another kind
        in the same knowledge sentence ("sábado" together with "9h"). Two times alone say nothing:
        "a equipe responde das 9h às 13h" matches the Saturday hours and is about something else.
        """
        mine = stems(sentence) - stems(fact.text)
        others = {f.ident for f in extract_facts(sentence) if f.kind != fact.kind}
        for words, idents in self._rows:
            if not any(_same(fact.ident, ident) for ident in idents):
                continue
            if any(self._frequency[w] <= DISTINCTIVE for w in mine & words) or others & idents:
                return True
        return False

    def unknown_words(self, sentence: str) -> list[str]:
        """Subject words of ``sentence`` that no sentence of the knowledge uses."""
        return sorted(stems(sentence) - self.vocabulary)


def _same(a: tuple[str, Key], b: tuple[str, Key]) -> bool:
    if a[0] != b[0]:
        return False
    return date_matches(a[1], b[1]) if a[0] == DATE else a[1] == b[1]


@lru_cache(maxsize=16)
def knowledge_text(text: str) -> KnowledgeText:
    return KnowledgeText(text)
