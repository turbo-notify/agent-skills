"""Whatever the arguments are, Workspace.path never leaves the workspace."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from tn_assist.paths import PathEscapeError, Workspace

ORG = "org_" + "a" * 32
NUM = "num_" + "b" * 32

_part = st.one_of(
    st.sampled_from(["..", ".", "", "a", "contacts", "../..", "/", "/etc", "x/../../y", "‮", "ａ"]),
    st.text(max_size=12),
)


@settings(max_examples=300, deadline=None)
@given(parts=st.lists(_part, max_size=5))
def test_path_is_inside_or_raises(parts: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace.open(Path(tmp), ORG, NUM, create=True)
        try:
            result = ws.path(*parts)
        except PathEscapeError:
            return
        assert os.path.commonpath([str(ws.root), str(result)]) == str(ws.root)
