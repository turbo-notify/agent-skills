"""Text the people around the assistant read, in Portuguese and English.

Every string follows the platform's customer-copy standards: written from the reader's side, one
idea per sentence, no dashes as punctuation, no internal vocabulary. A test scans them all.
"""

from __future__ import annotations

from typing import Literal

Lang = Literal["pt", "en"]

STRINGS: dict[str, dict[str, str]] = {
    "connected_number_warning": {
        "pt": (
            "O número conectado é o do celular, ou do WhatsApp Web, de quem atende os clientes. "
            "Se ele for administrador, qualquer atendente com o aparelho passa a ensinar o "
            "assistente, apagar clientes, mudar o modo e cadastrar outros administradores. "
            "O mais seguro é cadastrar como administradores os números pessoais de quem decide "
            "pelo negócio (o seu, por exemplo) e deixar o número conectado só para os "
            "atendentes. A escolha é sua. Cadastrar o número conectado como administrador?"
        ),
        "en": (
            "The connected number is the one on the phone, or WhatsApp Web, of the people who "
            "answer customers. If it is an admin, any attendant holding the device can teach the "
            "assistant, erase customers, change the mode and add other admins. The safer setup "
            "is to register as admins the personal numbers of the people who decide for the "
            "business (yours, for example) and keep the connected number for attendants. It is "
            "your call. Register the connected number as an admin?"
        ),
    },
    "connected_number_refused_over_whatsapp": {
        "pt": (
            "Esse é o número conectado. Quem está com o celular passaria a ter todos os poderes "
            "de administrador, por isso só quem configurou o assistente pode cadastrá-lo. "
            "Peça a essa pessoa."
        ),
        "en": (
            "That is the connected number. Whoever holds the phone would get every admin power, "
            "so only the person who set up the assistant can register it. Ask that person."
        ),
    },
    "greeting_default": {
        "pt": "Olá! Sou o atendente virtual de {business}. Como posso ajudar?",
        "en": "Hi! I am the virtual attendant of {business}. How can I help?",
    },
}


def t(key: str, lang: Lang, **fields: str) -> str:
    """The text for ``key`` in ``lang`` with ``fields`` filled in."""
    return STRINGS[key][lang].format(**fields)
