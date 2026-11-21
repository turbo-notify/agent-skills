# Reproducible proof that the attendant runs in a clean container with no agent program.
# Needs Docker. From the agent-skills folder:
#   docker run --rm -v "$PWD/skills/virtual-attendant:/skill:ro" \
#     -v "$PWD/tests/e2e/cloud_container_proof.py:/t/proof.py:ro" \
#     python:3.12-slim python /t/proof.py
# It starts a stand-in Turbo Notify API and a stand-in model inside the container (no keys).
"""Clean-container proof: the attendant runs with only Python, no Codex and no Claude Code.

Runs inside python:3.12-slim. Starts a fake Turbo Notify API and a fake model endpoint on localhost,
configures a workspace, starts the real runner (harness api) and checks that one customer message
is answered through the model and sent through the API.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SKILL = "/skill"
PKG = f"{SKILL}/scripts/tn_assist"
NUM = "num_" + "f" * 32
ORG = "org_" + "a" * 32
CONNECTED = "5571999294756"
CUSTOMER = "5511988887777"
TN_KEY = "tn_live_" + "k" * 32
LLM_KEY = "llm-test-key"
QUESTION = "Oi, vocês entregam hoje? Quanto custa o pão francês?"
state = {"armed": False, "served": False, "sent": [], "model": [], "tn_auth": set()}
EVENT = {
    "id": "evt_" + "1" * 32,
    "type": "message.received",
    "number_id": NUM,
    "created_at": "2026-11-21T22:00:00Z",
    "data": {
        "id": "msg_" + "2" * 32,
        "at": "2026-11-21T22:00:00Z",
        "content_type": "text",
        "preview": QUESTION[:40],
        "direction": "inbound",
        "origin": "native",
        "from": {"number": CUSTOMER, "name": "Ana Souza"},
        "to": {"number": CONNECTED},
    },
}
DECISION = {
    "reply": "Oi, Ana! Entregamos das 8h às 18h. O pão francês custa R$ 0,80 a unidade.",
    "facts": [{"key": "interest", "value": "pão francês"}],
    "escalate": None,
    "language": "pt",
    "state": None,
    "quote": None,
    "no_reply_needed": False,
}


class TN(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        state["tn_auth"].add(self.headers.get("authorization", ""))
        if self.path.startswith("/v1/events"):
            if state["armed"] and not state["served"]:
                state["served"] = True
                return self._json(200, {"data": [EVENT]})
            return self._json(200, {"data": []})
        if "/messages/" in self.path:
            return self._json(200, {"text": QUESTION})
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        state["tn_auth"].add(self.headers.get("authorization", ""))
        if self.path.endswith("/messages"):
            state["sent"].append(body)
            return self._json(202, {"id": "msg_" + "3" * 32})
        self._json(404, {"error": "not_found"})


class Model(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        state["model"].append((dict(self.headers), body))
        raw = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": json.dumps(DECISION)}}]}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def run(args, env, check=True):
    done = subprocess.run([sys.executable, PKG, *args], env=env, capture_output=True, text=True)
    if check and done.returncode not in (0, 2):
        print("FAILED:", args, done.returncode, done.stdout[-600:], done.stderr[-600:])
        sys.exit(1)
    return done


def main():
    assert shutil.which("codex") is None and shutil.which("claude") is None, "an agent is installed"
    tn_url, llm_url = serve(TN), serve(Model)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": "/tmp/home",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TN_ASSIST_BASE": "/tmp/data",
        "TN_ASSIST_ORG": ORG,
        "TN_ASSIST_NUMBER": NUM,
        "TURBO_NOTIFY_API_KEY": TN_KEY,
        "TURBO_NOTIFY_API_URL": tn_url,
        "TN_LLM_BASE_URL": llm_url,
        "TN_LLM_API_KEY": LLM_KEY,
        "TN_LLM_MODEL": "test-model",
    }
    os.makedirs("/tmp/home", exist_ok=True)
    run(
        [
            "init",
            "--connected",
            CONNECTED,
            "--business",
            "Padaria Aurora",
            "--lang",
            "pt",
            "--harness",
            "api",
            "--admin",
            "5571999258283",
            "--admin-name",
            "Dono",
        ],
        env,
    )
    with open("/tmp/pricing.md", "w") as f:
        f.write(
            "Pão francês: R$ 0,80 a unidade. Bolo de cenoura: R$ 45. Entregamos das 8h às 18h.\n"
        )
    run(
        [
            "kb-write",
            "--path",
            "pricing.md",
            "--visibility",
            "public",
            "--description",
            "precos e entrega",
            "--from-file",
            "/tmp/pricing.md",
        ],
        env,
    )
    run(["mode", "auto"], env)
    old = dict(EVENT, id="evt_" + "0" * 32, created_at="2026-11-21T21:00:00Z")
    old["data"] = dict(EVENT["data"], id="msg_" + "9" * 32, at="2026-11-21T21:00:00Z")
    with open("/tmp/page.json", "w") as f:
        json.dump({"data": [old]}, f)
    run(["cursor", "--from-page", "/tmp/page.json"], env)
    runner = subprocess.Popen(
        [sys.executable, PKG, "run", "--harness", "api", "--interval", "2"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(4)
    state["armed"] = True  # the customer writes now
    deadline = time.time() + 45
    while time.time() < deadline and not state["sent"]:
        time.sleep(1)
    runner.terminate()
    log = runner.communicate(timeout=10)[0]
    print("--- runner log tail:\n" + log[-700:])
    assert state["sent"], "the customer got no answer"
    print("--- model calls:", len(state["model"]))
    for root, _d, files in os.walk("/tmp/data"):
        for name in files:
            if name.endswith((".log", ".jsonl")) and ("runner" in name or "log" in root):
                print("--- file", os.path.join(root, name))
                with open(os.path.join(root, name), errors="replace") as log_file:
                    print(log_file.read()[-900:])
    if state["model"]:
        print("--- prompt tail:", state["model"][0][1]["messages"][0]["content"][-500:])
    sent = state["sent"][0]
    text = sent["content"]["text"]
    print("--- sent to", sent["to"], "\n" + text)
    assert sent["to"] == {"number": "+" + CUSTOMER}
    assert "R$ 0,80" in text and "8h às 18h" in text
    assert "IA" in text or "inteligência artificial" in text.lower() or "assistente" in text.lower()
    headers, body = state["model"][0]
    prompt = body["messages"][0]["content"]
    assert headers.get("Authorization", headers.get("authorization")) == f"Bearer {LLM_KEY}"
    assert (
        QUESTION in prompt and "R$ 0,80" in prompt
    )  # the model got the customer and the knowledge
    assert TN_KEY not in json.dumps(state["model"])  # ...and never the Turbo Notify key
    assert "tools" not in body  # a customer turn has no tools
    print("--- model calls:", len(state["model"]), "| TN auth headers seen:", len(state["tn_auth"]))
    print("OK: a customer was answered in a clean container with no agent program installed")


main()
