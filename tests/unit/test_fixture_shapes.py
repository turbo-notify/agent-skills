"""Factories keep the keys of real events (captured on the dev stack, values dropped)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.support import factories as f

REAL = json.loads((Path(__file__).parents[1] / "fixtures/events/real_shapes.json").read_text())


def keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: keys(v) for k, v in value.items()}
    return None


def test_inbound_text_has_the_real_keys() -> None:
    real = REAL["message.received|native|inbound|1:1|text"]
    mine = f.inbound_text()
    assert set(keys(mine)) == set(keys(real))
    assert set(mine["data"]) == set(real["data"])
    assert set(mine["data"]["from"]) == set(real["data"]["from"])


def test_group_has_group_id_and_no_number_in_to() -> None:
    real = REAL["message.received|native|inbound|group|text"]["data"]["to"]
    mine = f.inbound_group()["data"]["to"]
    assert set(mine) == set(real) == {"group_id", "name"}


def test_native_outbound_matches_real_party_keys() -> None:
    real = REAL["message.sent|native|outbound|1:1|text"]["data"]
    mine = f.native_outbound_to_contact()["data"]
    assert set(mine["from"]) == set(real["from"])
    assert set(mine["to"]) == set(real["to"])
