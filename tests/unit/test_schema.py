from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.support import factories as f
from tests.support.knowledge import seed_knowledge
from tests.support.workspace import make_workspace
from tn_assist.config import SCHEMA_VERSION, load_config, save_config
from tn_assist.memory import append_line
from tn_assist.paths import ContactKey, Workspace
from tn_assist.schema import MigrationFailed, WorkspaceTooNew, migrate
from tn_assist.store import read_json, write_json_atomic

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ANA = ContactKey.parse(f.ANA)


def populated(tmp_path: Path) -> Workspace:
    ws = make_workspace(tmp_path)
    seed_knowledge(ws)
    append_line(ws, ANA, role="contact", text="oi", event_id="evt_1", at="2026-10-17T10:00:00Z")
    write_json_atomic(ws.path("memory-policy.json"), {"pack_budget_contact": 4321})
    return ws


def add_marker(ws: Workspace) -> None:
    config = load_config(ws)
    config["migrated_marker"] = True
    save_config(ws, config)


def test_current_schema_is_a_no_op_without_a_backup(tmp_path: Path) -> None:
    ws = populated(tmp_path)
    report = migrate(ws, now=NOW)
    assert (report.from_version, report.to_version, report.backup) == (
        SCHEMA_VERSION,
        SCHEMA_VERSION,
        None,
    )
    assert not ws.path("backup").exists()


def test_migrate_backs_up_then_upgrades_and_keeps_every_file_readable(
    tmp_path: Path,
) -> None:  # AC6
    ws = populated(tmp_path)
    before = sorted(p.relative_to(ws.root).as_posix() for p in ws.root.rglob("*") if p.is_file())
    report = migrate(ws, now=NOW, target=2, migrations={1: add_marker})
    assert (report.from_version, report.to_version) == (1, 2)
    assert report.backup is not None and report.backup.is_dir()
    assert load_config(ws)["schema"] == 2 and load_config(ws)["migrated_marker"] is True
    for relative in before:
        if relative != "config.json":
            assert (ws.root / relative).exists(), relative
            assert (report.backup / relative).exists(), relative
    assert read_json(ws.path("memory-policy.json"), {}) == {"pack_budget_contact": 4321}


def test_migrate_twice_changes_nothing(tmp_path: Path) -> None:
    ws = populated(tmp_path)
    migrate(ws, now=NOW, target=2, migrations={1: add_marker})
    snapshot = {
        p.relative_to(ws.root).as_posix(): p.read_bytes()
        for p in ws.root.rglob("*")
        if p.is_file() and "backup" not in p.parts
    }
    again = migrate(ws, now=NOW, target=2, migrations={1: add_marker})
    assert again.backup is None and again.from_version == 2
    after = {
        p.relative_to(ws.root).as_posix(): p.read_bytes()
        for p in ws.root.rglob("*")
        if p.is_file() and "backup" not in p.parts
    }
    assert snapshot == after


def test_failed_migrate_leaves_the_previous_schema_intact(tmp_path: Path) -> None:
    ws = populated(tmp_path)
    original = {
        p.relative_to(ws.root).as_posix(): p.read_bytes() for p in ws.root.rglob("*") if p.is_file()
    }

    def boom(workspace: Workspace) -> None:
        add_marker(workspace)
        (workspace.root / "contacts" / f.ANA / "transcript.jsonl").write_text("corrupted")
        raise RuntimeError("step failed")

    with pytest.raises(MigrationFailed):
        migrate(ws, now=NOW, target=2, migrations={1: boom})
    assert load_config(ws)["schema"] == 1 and "migrated_marker" not in load_config(ws)
    restored = {
        p.relative_to(ws.root).as_posix(): p.read_bytes()
        for p in ws.root.rglob("*")
        if p.is_file() and "backup" not in p.parts
    }
    assert restored == original


def test_downgrade_refuses_a_newer_schema(tmp_path: Path) -> None:
    ws = populated(tmp_path)
    config = load_config(ws)
    config["schema"] = SCHEMA_VERSION + 1
    save_config(ws, config)
    with pytest.raises(WorkspaceTooNew):
        migrate(ws, now=NOW)


def test_a_missing_step_is_a_failure_not_a_silent_skip(tmp_path: Path) -> None:
    ws = populated(tmp_path)
    with pytest.raises(MigrationFailed):
        migrate(ws, now=NOW, target=3, migrations={1: add_marker})
    assert load_config(ws)["schema"] == 1
