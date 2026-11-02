# Turbo Notify Agent Skills

Skills that put the [Turbo Notify MCP server](https://docs.turbonotify.com/agents/mcp-server/) to work
inside your AI agent: Claude Code, Claude Desktop and Cowork, Codex and the Antigravity CLI, and any other
agent that reads the Agent Skills format.

## Skills

| Skill | What it does |
|---|---|
| `virtual-attendant` | Serves a business's customers on WhatsApp: answers in the customer's language, remembers each customer, calls a person when it should, and takes commands from administrators and attendants on WhatsApp itself. |

Customer guides (Portuguese and English): [WhatsApp virtual attendant](https://docs.turbonotify.com/en/guides/virtual-attendant/),
[Agent skills](https://docs.turbonotify.com/en/agents/skills/) and
[AI agents](https://docs.turbonotify.com/en/agents/overview/), with one page per agent.

## Install

| Agent | Commands |
|---|---|
| Claude Code | `claude plugin marketplace add turbo-notify/agent-skills#v1` then `claude plugin install turbo-notify@turbo-notify` |
| Claude Desktop and Cowork | Customize, Plugins, Add, Add marketplace, Add from a repository: `turbo-notify/agent-skills`; then Discover, `turbo-notify`, Add |
| Codex | `codex plugin marketplace add turbo-notify/agent-skills --ref v1` then `codex plugin add turbo-notify@turbo-notify` |
| Antigravity | `agy plugin install https://github.com/turbo-notify/agent-skills` |
| Hermes Agent | `hermes skills install turbo-notify/agent-skills/skills/virtual-attendant`, then add the MCP server `https://mcp.turbonotify.com` in Hermes |
| OpenCode | `npx skills add turbo-notify/agent-skills -a opencode` (project folder `.agents/skills/`, or `-g` for `~/.config/opencode/skills/`), then `opencode mcp add turbo-notify --url https://mcp.turbonotify.com` and `opencode mcp auth turbo-notify` to sign in. Guide: [Turbo Notify in OpenCode](https://docs.turbonotify.com/en/agents/opencode/) |
| Other agents that read the Agent Skills format (Cursor, Copilot and more) | `npx skills add turbo-notify/agent-skills`, then add the MCP server `https://mcp.turbonotify.com` in that agent's own settings |

The unattended runner exists for Claude Code and Codex only. In the other agents the attendant runs in
assisted mode: a session you keep open, where a person approves each draft.

`#v1` and `--ref v1` keep you on 1.x: a release that breaks compatibility never arrives by itself.
The plugin rows above already declare the Turbo Notify MCP server (`https://mcp.turbonotify.com`), so
there is nothing to add. Sign in with your Turbo Notify account the first time the agent uses it (Claude Code:
`/mcp`; Codex: `codex mcp login turbo-notify`; Antigravity: Agent settings, Customizations, Authenticate next to the server), then ask it to "set up the
Turbo Notify virtual attendant". No account yet? Create one at https://dashboard.turbonotify.com/auth/signup?locale=en. For anything
that runs alone, use an API key instead (see the guide for your agent in `skills/virtual-attendant/references/`).

## Update

The skill checks its version on every cycle and messages the administrators when a new one exists,
with the command for the agent in use.

| Agent | Command |
|---|---|
| Claude Code | `claude plugin marketplace update turbo-notify` then `claude plugin update turbo-notify@turbo-notify` |
| Claude Desktop and Cowork | The marketplace syncs from the repository; if a new version does not show up, remove the marketplace and add it again |
| Codex | `codex plugin marketplace upgrade` then `codex plugin add turbo-notify@turbo-notify` |
| Antigravity | Run the install command again |

## What it reaches

- The Turbo Notify MCP server at `https://mcp.turbonotify.com`, which the plugin declares in `.mcp.json`, `mcp.json` and `mcp_config.json` (one file for each family of agents; none holds a key or a header). You sign in with your account, or you connect your own with an API key. It runs with your account or API key permissions.
- One folder on your computer (by default `~/.turbo-notify/attendant`) holding, for each customer, the conversation (kept 90 days by default, then deleted), short summaries of older parts of it, and a file with the facts that customer gave (name, preferences, what they asked for), plus what you taught. Turbo Notify gets no copy. `/forget` erases one customer's data, and the retention is a number you can change in `memory-policy.json`.
- The Turbo Notify API at `https://api.turbonotify.com`, called by the helper with your API key when the unattended runner or a console command sends a message.
- One network address besides Turbo Notify: `raw.githubusercontent.com/turbo-notify/agent-skills/main/latest.json`, read about once an hour (and again after 10 minutes if a check fails) for the version check. A failure changes nothing.
- Your other MCP servers only if you allow each one during setup.

## What it runs and what it sends

- It runs the `tn_assist` helper with `python3`. The helper uses only the standard library and installs nothing.
- In the unattended runner (Claude Code and Codex), it starts your own `claude` or `codex` command once per customer, in a separate process with no tools, to write that customer's answer. The process gets that customer's messages, their file and the business knowledge that matches the message (the company, tone and hand-off files, plus up to three more picked by the words of the message), and nothing else.
- The text of a customer's message goes to the model of the agent you use, under your account with that agent. It goes nowhere else.
- The unattended runner reads your API key from the `TURBO_NOTIFY_API_KEY` variable you set for it and sends it only to `api.turbonotify.com`, as the sign-in of each request. It is never passed to the process that writes a customer's answer.
- Answers go out through the Turbo Notify API with your API key, to the customer who wrote. A check before every send stops another customer's number or name, non-public text and credentials.
- The first answer of each chat tells the customer that it comes from an AI. The line is added by the helper, not by the model.
- A customer who only says thanks, "ok" or sends a thumbs up gets no answer and is not handed to a person. The model has to ask for that, and the helper agrees only when the message is nothing but a thanks or an acknowledgement (no question mark, no number, a few words from a fixed list). Anything more is answered, or goes to a person.
- A customer goes to a person when the answer is not in what you taught (a price, stock, a deadline, a promise, a policy), when they ask for one or are upset, when the request is outside the business, when a payment problem, a refund or exchange, a complaint or a legal matter comes up, or when they ask the assistant to promise or confirm something in writing. A plain question about a rule you wrote, such as the exchange window, is answered.
- If the model's answer cannot be read, the helper asks again once with a line naming what was wrong, and a second failure in a row sends the customer a message saying a person will continue.

## Layout

```
.claude-plugin/        plugin and marketplace manifests (Claude Code, Codex)
plugin.json            manifest for Antigravity
.mcp.json              the Turbo Notify MCP server, as Claude Code, Claude Desktop, Cowork and Codex read it
mcp.json               the same server, as the Agent Plugins format reads it (Copilot, Cursor)
mcp_config.json        the same server, as Antigravity reads it
assets/icon.png        the plugin icon
agents/                the tool-less agent that answers a customer
skills/virtual-attendant/
  SKILL.md             the entry point the agent reads
  references/          one short file per topic (setup, tick, console, memory, privacy, ...)
  assets/templates/    interview templates (pt, en) and the memory policy
  scripts/tn_assist/   the helper: routing, memory, egress check, runner (Python 3.9+, no dependencies)
tools/release.py       version consistency check and sync
tests/                 unit, property and security tests (Poetry, development only)
evals/attendant/       live evaluation of a customer's turn on a real model, with a made-up shop
```

The helper holds the guarantees the model cannot be trusted with: who is who is decided by code from
the registered numbers, a customer's turn runs without tools, every reply passes an egress check
before it can leave, and the data never leaves the workspace folder.

## Development

```
poetry install
poetry run pytest
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy
python3 tools/release.py check
```

The live evaluation needs your own `claude` command, logged in. It asks 40 questions that the
knowledge cannot answer and 11 that it can, and plays seven conversations (some end with a thank
you, which is owed no answer). It checks every reply for a price, a date, a time, a quantity, a
discount or a promise the knowledge does not state, for the language of the reply, and that a
thank you is left alone while an "ok" that is really a question is answered. Two last checks cut the
model's answer in half to see what the runner does with an answer it cannot read, and play an "ok"
after an offer ("quer que eu chame alguém?") to see that it is answered, not left alone. Nothing in
it is real data.

```
poetry run python -m evals.attendant run                # writes evals/attendant/results/<date>-<label>/
poetry run python -m evals.attendant run --set refusal  # only the check on an unreadable answer
poetry run python -m evals.attendant run --set offers   # only the check on an ok that accepts an offer
TN_EVALS_LIVE=1 poetry run pytest evals                 # the same, as tests (skipped otherwise)
```

Releases bump `VERSION`, `latest.json`, the three manifests, the helper and pyproject versions and `CHANGELOG.md` together (`tools/release.py sync`),
then move the `v1` tag.

## License

MIT. See [LICENSE](LICENSE).
