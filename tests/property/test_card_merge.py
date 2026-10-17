from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.support import factories as f
from tests.support.workspace import make_workspace
from tn_assist.memory import CARD_CAP_TOKENS, Card, Fact
from tn_assist.paths import ContactKey
from tn_assist.tokens import estimate_tokens

KEY = ContactKey.parse(f.ANA)
fact = st.builds(
    Fact,
    key=st.sampled_from(
        ["name", "interest", "budget", "city", "objection", "stage", "k1", "k2", "k3"]
    ),
    value=st.text(min_size=1, max_size=800),
    source=st.sampled_from(["inferred", "attendant", "admin"]),
    at=st.sampled_from(["2026-10-17T10:00:00Z", "2026-10-17T11:00:00Z", "2026-10-17T12:00:00Z"]),
)


@settings(max_examples=200, deadline=None)
@given(facts=st.lists(fact, max_size=40))
def test_card_never_exceeds_cap_and_admin_facts_survive(facts: list[Fact]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = make_workspace(Path(tmp))
        card = Card.load(ws, KEY)
        for item in facts:
            card.merge([item])
        admin_keys = {x.key for x in facts if x.source == "admin"}
        stored = card.facts()
        # either under the cap, or nothing left to drop but administrator facts
        assert estimate_tokens(card.render()) <= CARD_CAP_TOKENS or all(
            x.source == "admin" for x in stored
        )
        assert admin_keys <= {x.key for x in stored}
        for key in admin_keys:
            assert card.get(key).source == "admin"  # type: ignore[union-attr]
