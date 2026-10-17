"""AC46: a marker from a source that is not public never reaches a customer."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.support import factories as f
from tests.support.knowledge import seed_knowledge
from tests.support.workspace import make_workspace
from tn_assist.egress import check_egress
from tn_assist.paths import ContactKey
from tn_assist.sources import SourceClass, SourceRegistry

ANA = ContactKey.parse(f.ANA)
_marker = st.text(alphabet="ABCDEFGHJKMNPQRSTUVWXYZ23456789-", min_size=8, max_size=16).map(
    lambda s: f"MK-{s}"
)
_padding = st.text(alphabet="abcdefghij klmnop,.!?", max_size=60)


def _case(text: str, how: int) -> str:
    """The marker as a reply might carry it: as is, lower case, upper case, spaced out."""
    return [text, text.lower(), text.upper(), " ".join(text)][how % 4]


@settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    marker=_marker,
    before=_padding,
    after=_padding,
    cls=st.sampled_from([SourceClass.INTERNAL, SourceClass.ADMIN_ONLY, SourceClass.OFF]),
    how=st.integers(0, 3),
)
def test_a_marker_of_a_non_public_source_is_always_blocked(
    marker: str, before: str, after: str, cls: SourceClass, how: int
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = make_workspace(Path(tmp))
        seed_knowledge(ws)
        registry = SourceRegistry.load(ws)
        registry.discovered(["crm"])
        registry.classify("crm", cls, confirmed=True)
        registry.add_marker("crm", marker)
        reply = f"{before} {_case(marker, how)} {after}"
        verdict = check_egress(ws, reply, contact=ANA)
        assert not verdict.ok and "non_public" in verdict.reasons


def test_an_unclassified_source_counts_as_off_and_a_public_one_does_not_block(
    tmp_path: Path,
) -> None:
    ws = make_workspace(tmp_path)
    seed_knowledge(ws)
    registry = SourceRegistry.load(ws)
    registry.discovered(["unknown", "catalog"])  # never classified: off
    registry.add_marker("unknown", "MK-UNCLASSIFIED-1")
    registry.classify("catalog", SourceClass.PUBLIC, confirmed=True)
    registry.add_marker("catalog", "MK-PUBLIC-1")
    assert "non_public" in check_egress(ws, "olha: MK-UNCLASSIFIED-1", contact=ANA).reasons
    assert check_egress(ws, "olha: MK-PUBLIC-1", contact=ANA).ok  # public is meant for customers
