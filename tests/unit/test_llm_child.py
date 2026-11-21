"""The ``api`` harness: one model turn over HTTP, with no agent program (cloud runner)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tn_assist.profiles import ADMIN_READ_TOOLS, KEY_ENV, build_child
from tn_assist.runner import ChildFailed, run_child

TN_KEY = "tn_live_" + "k" * 32
LLM_KEY = "llm-secret-key"
TOOLS = [
    {"name": "list_messages", "description": "Read messages.", "inputSchema": {"type": "object"}},
    {"name": "send_text", "description": "Send.", "inputSchema": {"type": "object"}},
]


class Fake:
    """A model endpoint and a tool server on one local port, recording what reached them."""

    def __init__(self) -> None:
        self.model_requests: list[tuple[dict[str, str], dict[str, Any]]] = []
        self.mcp_requests: list[tuple[dict[str, str], dict[str, Any]]] = []
        self.model_replies: list[dict[str, Any] | int] = []
        self.server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def fake() -> Iterator[Fake]:
    state = Fake()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, D102
            pass

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            headers = {k.lower(): v for k, v in self.headers.items()}
            if self.path.endswith("/chat/completions"):
                state.model_requests.append((headers, body))
                reply = state.model_replies.pop(0)
                if isinstance(reply, int):
                    self.send_response(reply)
                    self.end_headers()
                    return
                self._send(200, reply, "application/json")
            else:
                state.mcp_requests.append((headers, body))
                method = body.get("method")
                result: dict[str, Any] = {}
                if method == "tools/list":
                    result = {"tools": TOOLS}
                elif method == "tools/call":
                    result = {"content": [{"type": "text", "text": "two messages"}]}
                event = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result})
                self._send(200, event, "text/event-stream", sse=True)

        def _send(self, code: int, payload: Any, kind: str, sse: bool = False) -> None:
            raw = (f"event: message\ndata: {payload}\n\n" if sse else json.dumps(payload)).encode()
            self.send_response(code)
            self.send_header("content-type", kind)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    state.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=state.server.serve_forever, daemon=True)
    thread.start()
    yield state
    state.server.shutdown()


def say(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def ask_tool(name: str) -> dict[str, Any]:
    call = {"id": "call_1", "type": "function", "function": {"name": name, "arguments": "{}"}}
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}


def child(role: str, tmp_path: Path, fake: Fake, **env: str):  # type: ignore[no-untyped-def]
    base = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "TN_LLM_BASE_URL": fake.url,
        "TN_LLM_API_KEY": LLM_KEY,
        "TN_LLM_MODEL": "m",
        "SECRET_OTHER": "nope",
        **env,
    }
    return build_child(
        "api", role, "PROMPT", scratch=tmp_path, mcp_url=fake.url, key=TN_KEY, base_env=base
    )


def test_a_customer_turn_gets_the_model_key_and_never_the_turbo_notify_key(
    tmp_path: Path, fake: Fake
) -> None:
    fake.model_replies = [say('{"reply": "Oi"}')]
    c = child("contact", tmp_path, fake)
    assert KEY_ENV not in c.env and TN_KEY not in c.env.values() and "SECRET_OTHER" not in c.env
    assert "--tools" not in c.argv
    assert run_child(c, timeout=30).strip() == '{"reply": "Oi"}'
    headers, body = fake.model_requests[0]
    assert headers["authorization"] == f"Bearer {LLM_KEY}"
    # A real endpoint refused the default Python agent name (Groq, 2026-11-21): name ourselves.
    assert headers["user-agent"].startswith("tn-assist-api/")
    assert body["messages"] == [{"role": "user", "content": "PROMPT"}] and "tools" not in body
    assert fake.mcp_requests == []  # a customer turn never touches Turbo Notify


def test_an_administrator_turn_reads_through_the_read_only_tools_only(
    tmp_path: Path, fake: Fake
) -> None:
    fake.model_replies = [ask_tool("list_messages"), say("You have two messages.")]
    c = child("admin", tmp_path, fake)
    assert "--tools" in c.argv and c.env[KEY_ENV] == TN_KEY
    assert run_child(c, timeout=30).strip() == "You have two messages."
    offered = [t["function"]["name"] for t in fake.model_requests[0][1]["tools"]]
    assert offered == ["list_messages"] and set(offered) <= set(ADMIN_READ_TOOLS)
    calls = [b for _, b in fake.mcp_requests if b["method"] == "tools/call"]
    assert [c["params"]["name"] for c in calls] == ["list_messages"]
    assert fake.mcp_requests[0][0]["authorization"] == f"Bearer {TN_KEY}"
    followup = fake.model_requests[1][1]["messages"][-1]
    assert followup == {"role": "tool", "tool_call_id": "call_1", "content": "two messages"}


def test_a_write_tool_the_model_asks_for_is_refused_and_never_reaches_the_server(
    tmp_path: Path, fake: Fake
) -> None:
    fake.model_replies = [ask_tool("send_text"), say("Done.")]
    assert run_child(child("admin", tmp_path, fake), timeout=30).strip() == "Done."
    assert [b for _, b in fake.mcp_requests if b["method"] == "tools/call"] == []
    refused = fake.model_requests[1][1]["messages"][-1]["content"]
    assert refused == "This tool is not available."


def test_a_missing_model_key_or_a_model_error_fails_the_turn_without_leaking_a_key(
    tmp_path: Path, fake: Fake
) -> None:
    with pytest.raises(ChildFailed):
        run_child(child("contact", tmp_path, fake, TN_LLM_API_KEY=""), timeout=30)
    fake.model_replies = [500]
    with pytest.raises(ChildFailed):
        run_child(child("contact", tmp_path, fake), timeout=30)
    from tn_assist.llm_child import ChildError, answer

    fake.model_replies = [500]
    env = {"TN_LLM_BASE_URL": fake.url, "TN_LLM_API_KEY": LLM_KEY, "TN_LLM_MODEL": "m"}
    with pytest.raises(ChildError) as raised:
        answer("PROMPT", env, tools=False)
    assert LLM_KEY not in str(raised.value) and "500" in str(raised.value)
