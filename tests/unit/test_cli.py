from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from tests.support import factories as f
from tn_assist.cli import main

NOW = "2026-10-17T12:00:00Z"


def run(base: Path, *argv: str, stdin: str | None = None) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(
            [*argv],
            base=base,
            org=f.ORG,
            number=f.NUM,
            stdin=io.StringIO(stdin or ""),
        )
    return code, json.loads(out.getvalue())


def init(base: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    return run(
        base,
        "init",
        "--connected",
        f.CONNECTED,
        "--business",
        "Loja Aurora",
        "--lang",
        "pt",
        "--harness",
        "claude-code",
        "--admin",
        f.OWNER,
        "--now",
        NOW,
        *extra,
    )


def write(path: Path, payload: Any) -> str:
    path.write_text(json.dumps(payload))
    return str(path)


def test_init_creates_the_workspace_with_the_first_admin(tmp_path: Path) -> None:
    code, out = init(tmp_path)
    assert code == 0 and out["ok"] is True and out["existing"] is False
    assert (tmp_path / f.ORG / f.NUM / "config.json").exists()
    cfg = json.loads((tmp_path / f.ORG / f.NUM / "config.json").read_text())
    assert cfg["mode"] == "assist" and cfg["connected_number"] == f.CONNECTED
    assert run(tmp_path, "admin", "list")[1]["admins"] == [f.OWNER]


def test_init_twice_keeps_what_is_there(tmp_path: Path) -> None:  # AC32
    init(tmp_path)
    policy = tmp_path / f.ORG / f.NUM / "memory-policy.json"
    policy.write_text('{"pack_budget_contact": 1234}')
    knowledge = tmp_path / f.ORG / f.NUM / "knowledge" / "tone.md"
    knowledge.write_text("meu tom\n")
    code, out = init(tmp_path)
    assert code == 0 and out["existing"] is True
    assert json.loads(policy.read_text()) == {"pack_budget_contact": 1234}
    assert knowledge.read_text() == "meu tom\n"


def test_registering_the_connected_number_asks_for_confirmation_with_the_warning(
    tmp_path: Path,
) -> None:  # AC43
    code, out = init(tmp_path, "--admin", f.CONNECTED)
    assert code == 2 and out["error"] == "connected_number_needs_confirmation"
    assert "atendente" in out["warning"]
    assert (
        not (tmp_path / f.ORG / f.NUM / "admins.json").exists()
        or f.CONNECTED not in (tmp_path / f.ORG / f.NUM / "admins.json").read_text()
    )
    code, out = init(tmp_path, "--admin", f.CONNECTED, "--confirm-connected")
    assert code == 0
    _, listing = run(tmp_path, "admin", "list")
    assert f.CONNECTED in listing["admins"] and listing["connected_is_admin"] is True


def test_cursor_prime_and_fetch_plan(tmp_path: Path) -> None:
    init(tmp_path)
    _, empty = run(tmp_path, "cursor")
    assert empty["cursor"] is None and empty["params"] == {"limit": "1"}
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    _, after = run(tmp_path, "cursor")
    assert after["params"] == {"limit": "100", "ending_before": "evt_" + "0" * 32}


def test_fetch_plan_follows_the_full_page_rule(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "cursor", "--set", "evt_cursor")
    events = [f.inbound_text() for _ in range(100)]
    page1 = write(tmp_path / "p1.json", f.payload(*events, has_more=True))
    _, plan = run(tmp_path, "fetch-plan", "--page", page1)
    assert plan["done"] is False and plan["params"]["starting_after"] == events[-1]["id"]
    short = write(tmp_path / "p2.json", f.payload(f.inbound_text()))
    _, plan2 = run(tmp_path, "fetch-plan", "--page", page1, "--page", short)
    assert plan2["done"] is True


def test_the_whole_loop_ingest_next_pack_apply(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text(preview="quanto custa o plano anual?")
    page = write(tmp_path / "events.json", f.payload(event))
    code, ingested = run(tmp_path, "ingest", "--file", page, "--now", NOW)
    assert code == 0 and ingested["items"][0]["kind"] == "contact"

    _, leased = run(tmp_path, "next", "--kinds", "contact", "--now", NOW)
    item = leased["item"]
    assert item["contact"] == f.ANA and item["hydrate"] == [event["id"]]
    assert item["number_ref"] == f.NUM and item["message_ids"] == [event["data"]["id"]]

    text = tmp_path / "text.txt"
    text.write_text("Oi, quanto custa o plano anual?")
    run(tmp_path, "set-text", "--item", item["id"], "--event", event["id"], "--file", str(text))
    _, hydrated = run(tmp_path, "next-status", "--item", item["id"])
    assert hydrated["hydrate"] == []

    _, pack = run(tmp_path, "pack", "--item", item["id"], "--now", NOW)
    assert "Oi, quanto custa o plano anual?" in pack["pack"] and pack["tokens"] > 0

    decision = tmp_path / "decision.json"
    decision.write_text(
        json.dumps(
            {
                "reply": "Custa R$ 490.",
                "facts": [],
                "escalate": None,
                "language": "pt",
                "state": None,
            }
        )
    )
    code, applied = run(
        tmp_path, "apply", "--item", item["id"], "--decision-file", str(decision), "--now", NOW
    )
    assert code == 0
    ops = applied["deliveries"]
    assert any(
        o["op"] == "send" and o["to"] == f.OWNER and "#1" in o["text"] for o in ops
    )  # assisted mode
    _, again = run(tmp_path, "next", "--kinds", "contact", "--now", NOW)
    assert again["item"] is None


def test_auto_mode_replies_directly(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "mode", "auto")
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text(preview="oi")
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    _, leased = run(tmp_path, "next", "--kinds", "contact", "--now", NOW)
    decision = tmp_path / "d.json"
    decision.write_text('{"reply": "Olá!"}')
    _, applied = run(
        tmp_path,
        "apply",
        "--item",
        leased["item"]["id"],
        "--decision-file",
        str(decision),
        "--now",
        NOW,
    )
    assert applied["deliveries"] == [
        {
            "op": "reply",
            "message_id": event["data"]["id"],
            "text": "Olá!",
            "idempotency_key": event["id"],
            "contact": f.ANA,
        }
    ]


def test_invalid_decision_is_reported_not_applied(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text()
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    _, leased = run(tmp_path, "next", "--kinds", "contact", "--now", NOW)
    bad = tmp_path / "bad.json"
    bad.write_text('{"reply": "oi", "send_to": "5511999999999"}')
    code, out = run(
        tmp_path, "apply", "--item", leased["item"]["id"], "--decision-file", str(bad), "--now", NOW
    )
    assert code == 1 and out["error"] == "invalid_decision"
    _, status = run(tmp_path, "next-status", "--item", leased["item"]["id"])
    assert status["leased"] is True  # still leased: the caller decides whether to retry


def test_console_commands_run_without_a_model(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text(sender=f.OWNER, preview="/ajuda")
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    _, leased = run(tmp_path, "next", "--kinds", "admin", "--now", NOW)
    item = leased["item"]
    text = tmp_path / "t.txt"
    text.write_text("/ajuda")
    run(tmp_path, "set-text", "--item", item["id"], "--event", event["id"], "--file", str(text))
    code, out = run(tmp_path, "run-console", "--item", item["id"], "--now", NOW)
    assert code == 0 and out["needs_model"] is False
    assert any(
        o["op"] == "send" and o["to"] == f.OWNER and "/ensinar" in o["text"]
        for o in out["deliveries"]
    )


def test_free_text_from_an_admin_needs_the_model(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text(sender=f.OWNER, preview="quantos clientes tenho?")
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    _, leased = run(tmp_path, "next", "--kinds", "admin", "--now", NOW)
    item = leased["item"]
    text = tmp_path / "t.txt"
    text.write_text("quantos clientes tenho?")
    run(tmp_path, "set-text", "--item", item["id"], "--event", event["id"], "--file", str(text))
    _, out = run(tmp_path, "run-console", "--item", item["id"], "--now", NOW)
    assert out["needs_model"] is True and out["deliveries"] == []


def test_attendant_self_chat_command(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.self_chat_native(text="/ensinar tema")
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    _, leased = run(tmp_path, "next", "--kinds", "attendant", "--now", NOW)
    item = leased["item"]
    text = tmp_path / "t.txt"
    text.write_text("/ensinar tema")
    run(tmp_path, "set-text", "--item", item["id"], "--event", event["id"], "--file", str(text))
    _, out = run(tmp_path, "run-console", "--item", item["id"], "--now", NOW)
    assert (
        out["deliveries"][0]["to"] == f.CONNECTED
        and "administradores" in out["deliveries"][0]["text"]
    )


def test_kb_write_and_sources_for_the_configurator(tmp_path: Path) -> None:
    init(tmp_path)
    src = tmp_path / "pricing.txt"
    src.write_text("Plano anual: R$ 490.\n")
    code, out = run(
        tmp_path,
        "kb-write",
        "--path",
        "pricing.md",
        "--visibility",
        "public",
        "--description",
        "planos e precos",
        "--from-file",
        str(src),
    )
    assert code == 0 and out["path"] == "pricing.md"
    index = (tmp_path / f.ORG / f.NUM / "knowledge" / "INDEX.md").read_text()
    assert "- pricing.md: planos e precos" in index
    run(
        tmp_path,
        "kb-write",
        "--path",
        "margens.md",
        "--visibility",
        "internal",
        "--description",
        "margens",
        "--from-file",
        str(src),
    )
    assert (tmp_path / f.ORG / f.NUM / "knowledge" / "internal" / "margens.md").exists()
    run(tmp_path, "source", "discover", "--names", "catalogo,crm")
    code, listing = run(tmp_path, "source", "list")
    assert [s["name"] for s in listing["sources"]] == ["catalogo", "crm"]
    assert all(s["cls"] == "off" for s in listing["sources"])
    code, out = run(
        tmp_path, "source", "classify", "--name", "catalogo", "--cls", "public", "--confirmed"
    )
    assert code == 0


def test_kb_write_refuses_paths_outside_the_knowledge_folder(tmp_path: Path) -> None:
    init(tmp_path)
    src = tmp_path / "x.txt"
    src.write_text("x")
    for bad in ("../config.json", "/etc/passwd", "a/../../b.md"):
        code, out = run(
            tmp_path,
            "kb-write",
            "--path",
            bad,
            "--visibility",
            "public",
            "--description",
            "x",
            "--from-file",
            str(src),
        )
        assert code == 1 and out["ok"] is False, bad


def test_status_and_version_check_and_migrate(tmp_path: Path) -> None:
    init(tmp_path)
    code, status = run(tmp_path, "status", "--now", NOW)
    assert code == 0 and status["mode"] == "assist" and status["pending"] == 0
    code, version = run(
        tmp_path, "version-check", "--latest-json", '{"version": "9.9.9"}', "--now", NOW
    )
    assert (
        version["update"]["latest"] == "9.9.9"
        and "claude plugin update" in version["update"]["command"]
    )
    code, migrated = run(tmp_path, "migrate", "--now", NOW)
    assert migrated["from"] == migrated["to"]


def test_export_and_forget(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text()
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    code, out = run(tmp_path, "export", "--contact", f.ANA)
    assert (
        code == 0
        and out["contact"] == f.ANA
        and out["transcript"]
        and out["state"]["state"] == "active"
    )
    code, out = run(tmp_path, "forget", "--contact", f.ANA, "--now", NOW)
    assert code == 0
    assert not (tmp_path / f.ORG / f.NUM / "contacts" / f.ANA).exists()


def test_unknown_command_and_missing_workspace_are_clean_errors(tmp_path: Path) -> None:
    code, out = run(tmp_path, "status")
    assert code == 1 and out["ok"] is False and out["error"] == "no_workspace"
    with pytest.raises(SystemExit):
        run(tmp_path, "nope")


def test_it_runs_as_a_real_command_on_the_system_python(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = Path(__file__).parents[2] / "skills/virtual-attendant/scripts/tn_assist"
    env = {
        "PATH": "/usr/bin:/bin",
        "TN_ASSIST_BASE": str(tmp_path),
        "TN_ASSIST_ORG": f.ORG,
        "TN_ASSIST_NUMBER": f.NUM,
    }
    interpreter = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    out = subprocess.run(
        [
            interpreter,
            str(script),
            "init",
            "--connected",
            f.CONNECTED,
            "--business",
            "Loja",
            "--admin",
            f.OWNER,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["ok"] is True
    status = subprocess.run(
        [interpreter, str(script), "status"], capture_output=True, text=True, env=env, check=False
    )
    assert json.loads(status.stdout)["mode"] == "assist"


def test_a_long_conversation_queues_a_compaction_that_the_loop_can_run(tmp_path: Path) -> None:
    from tests.support.workspace import make_workspace  # noqa: F401
    from tn_assist.memory import append_line
    from tn_assist.paths import ContactKey, Workspace

    init(tmp_path)
    ws = Workspace.open(tmp_path, f.ORG, f.NUM)
    key = ContactKey.parse(f.ANA)
    for i in range(100):
        append_line(
            ws,
            key,
            role="contact" if i % 2 == 0 else "assistant",
            text=f"{i} " + "z" * 300,
            event_id=f"evt_h{i:04d}",
            at="2026-10-17T10:00:00Z",
        )
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text(preview="oi de novo")
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    _, leased = run(tmp_path, "next", "--kinds", "contact", "--now", NOW)
    decision = tmp_path / "d.json"
    decision.write_text('{"reply": "Oi!"}')
    run(
        tmp_path,
        "apply",
        "--item",
        leased["item"]["id"],
        "--decision-file",
        str(decision),
        "--now",
        NOW,
    )

    _, job = run(tmp_path, "next", "--kinds", "compaction", "--now", NOW)
    item = job["item"]
    assert item is not None and item["kind"] == "compaction" and item["hydrate"] == []
    _, pack = run(tmp_path, "pack", "--item", item["id"], "--now", NOW)
    assert "0 zzz" in pack["pack"] and '"summary"' in pack["pack"]
    result = tmp_path / "c.json"
    result.write_text('{"summary": "Resumo da conversa.", "facts": []}')
    code, out = run(
        tmp_path, "apply", "--item", item["id"], "--decision-file", str(result), "--now", NOW
    )
    assert code == 0 and out["deliveries"] == []
    assert list((tmp_path / f.ORG / f.NUM / "contacts" / f.ANA / "episodes").glob("*.md"))


def _admin_item(
    tmp_path: Path, text: str = "pode apagar a Ana?"
) -> tuple[dict[str, Any], dict[str, Any]]:
    init(tmp_path)
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text(sender=f.OWNER, preview=text)
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    _, leased = run(tmp_path, "next", "--kinds", "admin", "--now", NOW)
    return leased["item"], event


def _apply_model_command(tmp_path: Path, command: str) -> dict[str, Any]:
    item, _ = _admin_item(tmp_path)
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"reply": "ok", "command": command}))
    code, out = run(
        tmp_path, "apply", "--item", item["id"], "--decision-file", str(decision), "--now", NOW
    )
    assert code == 0
    return out


def test_a_model_proposed_command_that_changes_something_is_only_a_suggestion(
    tmp_path: Path,
) -> None:  # security review
    ws_root = tmp_path / f.ORG / f.NUM
    for command in (
        f"/esquecer {f.ANA}",
        f"/admin add {f.BRUNO}",
        f"/admin remover {f.OWNER}",
        "/modo auto",
        "/pausar",
        "/idioma en",
        "/kb remover faq",
        "/confirmar 1234",
    ):
        out = _apply_model_command(tmp_path / command.replace("/", "_").replace(" ", "-"), command)
        texts = " ".join(o["text"] for o in out["deliveries"])
        assert "envie você mesmo" in texts or command == "/confirmar 1234", command
    for folder in tmp_path.iterdir():
        cfg = json.loads((folder / f.ORG / f.NUM / "config.json").read_text())
        assert cfg["mode"] == "assist" and cfg["lang"] == "pt" and cfg["paused"] is False
        assert (
            json.loads((folder / f.ORG / f.NUM / "admins.json").read_text())["admins"][0]["number"]
            == f.OWNER
        )
    assert ws_root.exists() is False


def test_a_model_proposed_read_only_command_runs(tmp_path: Path) -> None:
    out = _apply_model_command(tmp_path, "/status")
    assert any("Modo" in o["text"] for o in out["deliveries"])


def test_a_customer_can_be_handed_to_a_person_without_a_model_answer(tmp_path: Path) -> None:
    init(tmp_path)
    run(tmp_path, "mode", "auto")
    run(tmp_path, "cursor", "--set", "evt_" + "0" * 32)
    event = f.inbound_text(preview="oi")
    run(tmp_path, "ingest", "--file", write(tmp_path / "e.json", f.payload(event)), "--now", NOW)
    _, leased = run(tmp_path, "next", "--kinds", "contact", "--now", NOW)
    code, out = run(
        tmp_path,
        "apply",
        "--item",
        leased["item"]["id"],
        "--escalate",
        "decision unreadable",
        "--now",
        NOW,
    )
    assert code == 0
    kinds = {o["op"] for o in out["deliveries"]}
    assert kinds == {"reply", "send"}
    sends = [o for o in out["deliveries"] if o["op"] == "send"]
    assert {o["to"] for o in sends} == {f.OWNER, f.CONNECTED}
    assert "decision unreadable" in sends[0]["text"]
    _, queue = run(tmp_path, "status", "--now", NOW)
    assert queue["waiting_for_a_person"] == 1
