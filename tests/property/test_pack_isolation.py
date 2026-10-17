"""A contact's pack never carries bytes that belong to another contact or another number."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.support import factories as f
from tests.support.knowledge import seed_knowledge
from tests.support.workspace import make_workspace
from tn_assist.events import parse_events
from tn_assist.ingest import ingest
from tn_assist.memory import Card, Fact, append_line, write_episode
from tn_assist.pack import build_pack
from tn_assist.paths import ContactKey
from tn_assist.queue import WorkItem, enqueue

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ANA, BRUNO = ContactKey.parse(f.ANA), ContactKey.parse(f.BRUNO)
token = st.text(alphabet="0123456789abcdef", min_size=16, max_size=16).map(lambda s: "ZQ" + s)


@settings(max_examples=60, deadline=None)
@given(secrets=st.lists(token, min_size=4, max_size=4, unique=True), turns=st.integers(0, 30))
def test_pack_for_b_never_contains_a_secret(secrets: list[str], turns: int) -> None:
    transcript_secret, card_secret, episode_secret, other_ws_secret = secrets
    with tempfile.TemporaryDirectory() as tmp:
        ws = make_workspace(Path(tmp) / "one")
        other = make_workspace(Path(tmp) / "two")
        seed_knowledge(ws)
        for i in range(turns):
            append_line(
                ws,
                ANA,
                role="contact",
                text=f"{transcript_secret} {i}",
                event_id=f"evt_a{i}",
                at="2026-10-17T10:00:00Z",
            )
        card = Card.load(ws, ANA)
        card.merge([Fact("k", card_secret, "admin", "2026-10-17T10:00:00Z")])
        card.save()
        write_episode(ws, ANA, episode_secret, covers=("evt_a0", "evt_a1"))
        append_line(
            other,
            ANA,
            role="contact",
            text=other_ws_secret,
            event_id="evt_o1",
            at="2026-10-17T10:00:00Z",
        )
        event = f.inbound_text(
            sender=f.BRUNO, sender_name="Bruno Lima", preview="oi, quero o plano anual"
        )
        ingest(ws, parse_events(f.payload(event)), NOW)
        item = enqueue(ws, WorkItem.new("contact", f.BRUNO, (event["id"],), NOW))
        text = build_pack(ws, item, now=NOW).text
        for secret in secrets:
            assert secret not in text
        assert f.ANA not in text and "Ana Souza" not in text
