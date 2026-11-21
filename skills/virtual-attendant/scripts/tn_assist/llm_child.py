"""One model turn over HTTP, for the unattended runner (harness ``api``).

Reads the prompt on standard input and prints the model's answer on standard output, like the
``claude -p`` and ``codex exec`` children, but without any agent program: it only needs Python and
network access. It talks to ONE model endpoint that speaks the OpenAI chat completions protocol
(OpenAI, Groq, a self-hosted server) and, for an administrator or
attendant turn, to the Turbo Notify MCP server with the read-only tools of the profile.

It runs nothing the model says: there is no shell, no file tool and no other server, so what a
customer writes can change the text of the answer and nothing else. The runner still checks the
answer before anything is sent.

Configuration comes only from the environment the runner builds:

* ``TN_LLM_API_KEY`` and ``TN_LLM_MODEL``: required. ``TN_LLM_BASE_URL``: defaults to OpenAI.
* ``TN_LLM_TIMEOUT``: seconds for one request (default 120).
* with ``--tools``: ``TURBO_NOTIFY_API_KEY`` and ``TN_MCP_URL``.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from tn_assist.profiles import ADMIN_READ_TOOLS, KEY_ENV

DEFAULT_BASE = "https://api.openai.com/v1"
MAX_TURNS = 8
TOOL_TEXT_LIMIT = 20_000
MCP_VERSION = "2025-06-18"


class ChildError(Exception):
    """A failure the runner should retry later; the message never carries a key."""


def _post(
    url: str, body: dict[str, Any], headers: dict[str, str], timeout: float
) -> tuple[bytes, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        url, data=data, headers={"content-type": "application/json", **headers}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read(), response.headers
    except urllib.error.HTTPError as error:
        raise ChildError(f"http {error.code} from {urllib.parse.urlsplit(url).netloc}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ChildError(
            f"unreachable {urllib.parse.urlsplit(url).netloc}: {type(error).__name__}"
        ) from None


def _json_or_event(raw: bytes) -> dict[str, Any]:
    """A JSON-RPC answer comes as plain JSON or as one server-sent event."""
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("{"):
        return json.loads(text)  # type: ignore[no-any-return]
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())  # type: ignore[no-any-return]
    raise ChildError("unreadable answer from the tool server")


class Mcp:
    """The few JSON-RPC calls the read-only tools need, with the key only in this process."""

    def __init__(self, url: str, key: str, timeout: float) -> None:
        self._url = url
        self._timeout = timeout
        self._headers = {
            "authorization": f"Bearer {key}",
            "accept": "application/json, text/event-stream",
        }
        self._id = 0
        self._session: str | None = None
        self._call(
            "initialize",
            {
                "protocolVersion": MCP_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "tn-assist-api", "version": "1"},
            },
        )

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        headers = dict(self._headers)
        if self._session:
            headers["mcp-session-id"] = self._session
        raw, response_headers = _post(
            self._url,
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
            headers,
            self._timeout,
        )
        session = response_headers.get("mcp-session-id")
        if session:
            self._session = session
        answer = _json_or_event(raw)
        if "error" in answer:
            raise ChildError(f"tool server refused {method}")
        return dict(answer.get("result", {}))

    def tools(self) -> list[dict[str, Any]]:
        listed = self._call("tools/list", {}).get("tools", [])
        return [t for t in listed if t.get("name") in ADMIN_READ_TOOLS]

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in ADMIN_READ_TOOLS:
            return "This tool is not available."
        result = self._call("tools/call", {"name": name, "arguments": arguments})
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        text = "\n".join(parts) or json.dumps(result.get("structuredContent", {}))
        return text[:TOOL_TEXT_LIMIT]


def _function(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": str(tool.get("description", ""))[:1000],
            "parameters": schema,
        },
    }


def answer(prompt: str, env: dict[str, str], *, tools: bool) -> str:
    base = env.get("TN_LLM_BASE_URL", DEFAULT_BASE).rstrip("/")
    key = env.get("TN_LLM_API_KEY", "")
    model = env.get("TN_LLM_MODEL", "")
    if not key or not model:
        raise ChildError("TN_LLM_API_KEY and TN_LLM_MODEL are required")
    timeout = float(env.get("TN_LLM_TIMEOUT", "120"))
    headers = {"authorization": f"Bearer {key}"}
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    functions: list[dict[str, Any]] = []
    mcp: Mcp | None = None
    if tools:
        tn_key = env.get(KEY_ENV, "")
        if not tn_key:
            raise ChildError("an administrator turn needs the Turbo Notify key")
        mcp = Mcp(env.get("TN_MCP_URL", "https://mcp.turbonotify.com"), tn_key, timeout)
        functions = [_function(t) for t in mcp.tools()]
    for _ in range(MAX_TURNS if functions else 1):
        body: dict[str, Any] = {"model": model, "messages": messages}
        if functions:
            body["tools"] = functions
        raw, _ = _post(f"{base}/chat/completions", body, headers, timeout)
        choices = json.loads(raw).get("choices") or []
        if not choices:
            raise ChildError("the model returned no answer")
        message = choices[0].get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls or mcp is None:
            return str(message.get("content") or "").strip()
        messages.append(message)
        for call in calls:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except ValueError:
                arguments = {}
            text = mcp.run(
                str(function.get("name", "")), arguments if isinstance(arguments, dict) else {}
            )
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})
    raise ChildError("the model kept asking for tools")


def main(argv: list[str]) -> int:
    try:
        print(answer(sys.stdin.read(), dict(os.environ), tools="--tools" in argv))
    except ChildError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
