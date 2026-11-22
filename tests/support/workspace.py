"""Helpers to build a bound workspace for tests."""

from __future__ import annotations

from pathlib import Path

from tests.support import factories as f
from tn_assist.config import save_config
from tn_assist.paths import Workspace
from tn_assist.store import write_json_atomic


def make_workspace(
    tmp_path: Path, *, admins: tuple[str, ...] = (f.OWNER,), ai_notice: bool = True
) -> Workspace:
    """A bound workspace. The AI notice is ON here so the many tests that read what a customer
    receives keep covering it; the shipped default is OFF (see test_ai_notice.py)."""
    ws = Workspace.open(tmp_path, f.ORG, f.NUM, create=True)
    save_config(
        ws,
        {
            "schema": 1,
            "bound_number_id": f.NUM,
            "connected_number": f.CONNECTED,
            "lang": "pt",
            "mode": "assist",
            "ai_notice": ai_notice,
        },
    )
    write_json_atomic(
        ws.path("admins.json"),
        {"schema": 1, "admins": [{"number": n, "name": None} for n in admins]},
    )
    return ws
