from __future__ import annotations

from tests.support import copy_rules
from tn_assist.i18n import STRINGS, t


def test_every_key_has_both_languages() -> None:
    for key, by_lang in STRINGS.items():
        assert set(by_lang) == {"pt", "en"}, key


def test_user_visible_strings_pass_copy_guards() -> None:
    triples = [
        (key, lang, text) for key, by_lang in STRINGS.items() for lang, text in by_lang.items()
    ]
    assert copy_rules.scan(triples) == []


def test_connected_number_warning_explains_the_separation() -> None:
    pt, en = t("connected_number_warning", "pt"), t("connected_number_warning", "en")
    assert "administrador" in pt and "atendente" in pt
    assert "admins" in en and "attendants" in en


def test_fields_are_filled_in() -> None:
    assert "Ana" in t("greeting_default", "pt", business="Ana")
