"""AC16: two organizations on one machine, running at once, never touch each other's data."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.support import factories as f
from tests.support.fakes import FakeApi, FakeHarness
from tn_assist.config import save_config
from tn_assist.paths import Workspace
from tn_assist.runner import RunnerConfig, run_cycle
from tn_assist.store import write_json_atomic

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ORG_A, NUM_A, KEY_A = "org_" + "a" * 32, "num_" + "a" * 32, "tn_live_" + "A" * 32
ORG_B, NUM_B, KEY_B = "org_" + "b" * 32, "num_" + "b" * 32, "tn_live_" + "B" * 32
MARK_A, MARK_B = "SECRETO-DA-LOJA-ALFA", "SECRETO-DA-LOJA-BETA"


def tenant(
    root: Path, org: str, num: str, key: str, marker: str
) -> tuple[Workspace, RunnerConfig, FakeApi]:
    ws = Workspace.open(root, org, num, create=True)
    save_config(
        ws,
        {
            "schema": 1,
            "bound_number_id": num,
            "connected_number": f.CONNECTED,
            "lang": "pt",
            "mode": "auto",
            "business_name": marker,
        },
    )
    write_json_atomic(
        ws.path("admins.json"), {"schema": 1, "admins": [{"number": f.OWNER, "name": None}]}
    )
    for name, text in (("company.md", f"Somos a {marker}.\n"),):
        path = ws.path("knowledge", name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    api = FakeApi()
    api.add(f.inbound_text(number_id=num, preview="mensagem antiga"))
    cfg = RunnerConfig(ws, "claude-code", api_key=key, env={"HOME": "/home/x", "PATH": "/usr/bin"})
    run_cycle(cfg, api, spawn=FakeHarness(), now=T0, fetch_version=None)  # prime the cursor
    return ws, cfg, api


def test_two_organizations_running_together_stay_apart(tmp_path: Path) -> None:
    a = tenant(tmp_path, ORG_A, NUM_A, KEY_A, MARK_A)
    b = tenant(tmp_path, ORG_B, NUM_B, KEY_B, MARK_B)
    harnesses: dict[str, FakeHarness] = {}
    errors: list[BaseException] = []

    def work(name: str, world: tuple[Workspace, RunnerConfig, FakeApi], number_id: str) -> None:
        ws, cfg, api = world
        try:
            api.add(
                f.inbound_text(
                    number_id=number_id,
                    preview=f"quero comprar na {name}",
                    created_at="2026-10-17T12:01:00Z",
                )
            )
            harness = FakeHarness(f'{{"reply": "Bem-vindo à {name}!"}}')
            harnesses[name] = harness
            for minute in range(1, 4):
                run_cycle(
                    cfg, api, spawn=harness, now=T0 + timedelta(minutes=minute), fetch_version=None
                )
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    threads = [
        threading.Thread(target=work, args=("ALFA", a, NUM_A)),
        threading.Thread(target=work, args=("BETA", b, NUM_B)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

    for own, world, foreign_marker, key, foreign_key, foreign_ids in (
        ("ALFA", a, MARK_B, KEY_A, KEY_B, (ORG_B, NUM_B)),
        ("BETA", b, MARK_A, KEY_B, KEY_A, (ORG_A, NUM_A)),
    ):
        ws, cfg, api = world
        assert [r[2] for r in api.replies] == [f.disclosed(f"Bem-vindo à {own}!")]
        assert all(r[0] == ws.num_id for r in api.replies + api.sends)  # only its own number
        for child in harnesses[own].children:
            assert foreign_marker not in child.stdin and foreign_key not in str(child.env)
            assert KEY_A not in child.stdin and KEY_B not in child.stdin  # no key in a prompt
        for path in ws.root.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert foreign_marker not in text and foreign_key not in text, path
                assert all(i not in text for i in foreign_ids), path
        assert (cfg.api_key or "") == key
