from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.workspace import make_workspace
from tn_assist.sources import (
    NeedsConfirmation,
    SourceClass,
    SourceRegistry,
    WriteToolError,
    is_probably_write,
)


def reg(tmp_path: Path) -> SourceRegistry:
    return SourceRegistry.load(make_workspace(tmp_path))


def test_unclassified_source_is_off(tmp_path: Path) -> None:  # AC45
    r = reg(tmp_path)
    r.discovered(["catalogo", "crm"])
    assert r.get("catalogo").cls is SourceClass.OFF  # type: ignore[union-attr]
    assert r.allowed_for("contact", assisted=True, in_session=True) == []
    assert r.allowed_for("admin", assisted=False, in_session=True) == []


def test_discovered_does_not_reset_a_known_source(tmp_path: Path) -> None:
    r = reg(tmp_path)
    r.discovered(["catalogo"])
    r.classify("catalogo", SourceClass.PUBLIC, confirmed=True)
    r.discovered(["catalogo", "outro"])
    assert SourceRegistry.load(r.ws).get("catalogo").cls is SourceClass.PUBLIC  # type: ignore[union-attr]


def test_widening_needs_confirmation_narrowing_does_not(tmp_path: Path) -> None:
    r = reg(tmp_path)
    r.discovered(["crm"])
    with pytest.raises(NeedsConfirmation):
        r.classify("crm", SourceClass.INTERNAL)
    r.classify("crm", SourceClass.INTERNAL, confirmed=True)
    r.classify("crm", SourceClass.ADMIN_ONLY)  # narrowing
    r.classify("crm", SourceClass.OFF)
    assert r.get("crm").cls is SourceClass.OFF  # type: ignore[union-attr]


def test_going_live_or_dropping_personal_widens(tmp_path: Path) -> None:
    r = reg(tmp_path)
    r.discovered(["estoque"])
    r.classify("estoque", SourceClass.PUBLIC, personal=True, confirmed=True)
    with pytest.raises(NeedsConfirmation):
        r.classify("estoque", SourceClass.PUBLIC, personal=False)
    with pytest.raises(NeedsConfirmation):
        r.classify(
            "estoque", SourceClass.PUBLIC, personal=True, mode="live", live_tools=("get_stock",)
        )


def test_only_public_sources_reach_the_contact_role(tmp_path: Path) -> None:
    r = reg(tmp_path)
    r.discovered(["catalogo", "crm", "financeiro"])
    r.classify("catalogo", SourceClass.PUBLIC, confirmed=True)
    r.classify("crm", SourceClass.INTERNAL, confirmed=True)
    r.classify("financeiro", SourceClass.ADMIN_ONLY, confirmed=True)

    def names(role: str) -> list[str]:
        found = r.allowed_for(role, assisted=False, in_session=False)  # type: ignore[arg-type]
        return sorted(s.name for s in found)

    assert names("contact") == []  # snapshot only; no live tool for a contact outside a session
    assert names("attendant") == ["catalogo", "crm"]
    assert names("admin") == ["catalogo", "crm", "financeiro"]


def test_contact_live_lookup_only_in_session_for_public_live_sources(tmp_path: Path) -> None:
    r = reg(tmp_path)
    r.discovered(["estoque", "catalogo"])
    r.classify(
        "estoque", SourceClass.PUBLIC, mode="live", live_tools=("get_stock",), confirmed=True
    )
    r.classify("catalogo", SourceClass.PUBLIC, confirmed=True)
    in_session = r.allowed_for("contact", assisted=False, in_session=True)
    assert [s.name for s in in_session] == ["estoque"]
    assert r.allowed_for("contact", assisted=False, in_session=False) == []


def test_personal_source_only_in_assisted_contact_turns(tmp_path: Path) -> None:  # AC50
    r = reg(tmp_path)
    r.discovered(["pedidos"])
    r.classify(
        "pedidos",
        SourceClass.PUBLIC,
        mode="live",
        live_tools=("get_order",),
        personal=True,
        confirmed=True,
    )
    assert r.allowed_for("contact", assisted=False, in_session=True) == []
    assert [s.name for s in r.allowed_for("contact", assisted=True, in_session=True)] == ["pedidos"]


def test_write_tools_never_allowed(tmp_path: Path) -> None:  # AC47
    r = reg(tmp_path)
    r.discovered(["crm"])
    for tool in (
        "delete_customer",
        "send_message",
        "create_order",
        "updateStock",
        "cancel_order",
        "pay_invoice",
    ):
        assert is_probably_write(tool)
        with pytest.raises(WriteToolError):
            r.classify("crm", SourceClass.PUBLIC, mode="live", live_tools=(tool,), confirmed=True)
    assert not is_probably_write("get_stock") and not is_probably_write("list_products")


def test_unknown_source_cannot_be_classified(tmp_path: Path) -> None:
    r = reg(tmp_path)
    with pytest.raises(KeyError):
        r.classify("ghost", SourceClass.PUBLIC, confirmed=True)


def test_non_public_markers_are_listed(tmp_path: Path) -> None:
    r = reg(tmp_path)
    r.discovered(["crm"])
    r.classify("crm", SourceClass.INTERNAL, confirmed=True)
    r.add_marker("crm", "CRM-CANARY-1")
    assert "CRM-CANARY-1" in SourceRegistry.load(r.ws).non_public_markers()
