"""The AI notice is the business's choice: off by default, one line when it turns it on."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tests.support import factories as f
from tests.support.workspace import make_workspace
from tests.unit.test_apply import arrive, decide
from tn_assist.apply import apply_contact_decision
from tn_assist.cli import main
from tn_assist.config import load_config

T0 = datetime(2026, 11, 21, 22, 0, tzinfo=timezone.utc)


def texts(actions) -> list[str]:  # type: ignore[no-untyped-def]
    return [a.text for a in actions if hasattr(a, "text")]


def test_by_default_the_first_answer_of_a_chat_does_not_announce_an_ai(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, ai_notice=False)
    item, _ = arrive(ws)
    actions = apply_contact_decision(ws, item, decide(reply="Custa R$ 49."), T0, mode="auto")
    assert texts(actions) == ["Custa R$ 49."]


def test_a_workspace_with_no_setting_at_all_does_not_announce_an_ai(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    config = load_config(ws)
    del config["ai_notice"]
    from tn_assist.config import save_config

    save_config(ws, config)
    item, _ = arrive(ws)
    actions = apply_contact_decision(ws, item, decide(reply="Custa R$ 49."), T0, mode="auto")
    assert texts(actions) == ["Custa R$ 49."]


def test_turned_on_it_opens_the_first_answer_only(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, ai_notice=True)
    item, _ = arrive(ws)
    actions = apply_contact_decision(ws, item, decide(reply="Custa R$ 49."), T0, mode="auto")
    assert texts(actions) == [f.disclosed("Custa R$ 49.")]


def test_the_command_turns_it_on_and_off_and_status_shows_it(
    tmp_path: Path,
    capsys,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    ws = make_workspace(tmp_path, ai_notice=False)
    base = ["--base", str(tmp_path), "--org", f.ORG, "--number", f.NUM]
    assert main([*base, "ai-notice", "on"]) == 0
    assert load_config(ws)["ai_notice"] is True
    assert main([*base, "status"]) == 0
    assert '"ai_notice": true' in capsys.readouterr().out
    assert main([*base, "ai-notice", "off"]) == 0
    assert load_config(ws)["ai_notice"] is False
