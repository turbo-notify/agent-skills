# Server or container (harness `api`)

The unattended runner can run on any computer that stays on and has Python 3.9 or newer, with **no agent program and no login**: a small server, a container, a Raspberry Pi. Each model turn is one HTTP call to a model endpoint that speaks the OpenAI chat completions protocol (OpenAI, Groq, a self-hosted server). Nothing in this mode depends on an agent program or on a person's computer.

## What it needs

- `TURBO_NOTIFY_API_KEY` (a key made with the **AI agent (MCP)** template), `TN_ASSIST_ORG`, `TN_ASSIST_NUMBER`, and a folder that survives restarts (`TN_ASSIST_BASE`, a volume in a container): it keeps the cursor, the queue, the memory and the knowledge.
- `TN_LLM_API_KEY` and `TN_LLM_MODEL`. `TN_LLM_BASE_URL` defaults to OpenAI's; for Groq it is `https://api.groq.com/openai/v1`. `--model` overrides `TN_LLM_MODEL`.

## Set up once, then run

Set the workspace up as [setup.md](setup.md) says (with any agent, on any computer), with `--harness api`, then give the same folder to the server. In a container:

```
docker build -f assets/cloud/Dockerfile -t tn-attendant <skill folder>
docker run -d --restart unless-stopped -v attendant:/data \
  -e TURBO_NOTIFY_API_KEY -e TN_ASSIST_ORG -e TN_ASSIST_NUMBER \
  -e TN_LLM_API_KEY -e TN_LLM_MODEL tn-attendant
```

Administrators teach the business and change prices from WhatsApp with the console commands ([admin-console.md](admin-console.md)), so the workspace does not need to be reached again.

## How it stays safe

- A **customer** turn has one model key and no tool, no Turbo Notify key, no file and no shell: the child cannot do anything except return text, and the helper checks that text before anything is sent.
- An **administrator or attendant** turn also gets the Turbo Notify key, and only the read tools of [profiles.py](../scripts/tn_assist/profiles.py): a write tool the model asks for is refused inside the child.
- The child imports only the standard library, opens no file and starts no program (a test guards it). It is more restricted than the agent programs of the other harnesses.

## What it cannot do

- It does not survive losing the folder: use a volume or a disk that is backed up. A host that wipes the disk on restart answers again from zero and hands customers over to a person.
- The model sees the customer's message and the business knowledge, so the model provider receives them: choose it with that in mind and say so in the privacy notice ([privacy.md](privacy.md)).
- Tested in a clean container with a stand-in model and a stand-in Turbo Notify API; run one real turn with your own model key before relying on it.
