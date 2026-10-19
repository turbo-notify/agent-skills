# Turbo Notify Agent Skills

Skills that put the [Turbo Notify MCP server](https://docs.turbonotify.com/general/mcp-server/) to work
inside your AI agent: Claude Code, Claude Desktop and Cowork, Codex and the Antigravity CLI.

## Skills

| Skill | What it does |
|---|---|
| `virtual-attendant` | Serves a business's customers on WhatsApp: answers in the customer's language, remembers each customer, calls a person when it should, and takes commands from administrators and attendants on WhatsApp itself. |

Customer guides (Portuguese and English): [WhatsApp virtual attendant](https://docs.turbonotify.com/en/guides/virtual-attendant/)
and [Agent skills](https://docs.turbonotify.com/en/general/skills/).

## Install

| Agent | Commands |
|---|---|
| Claude Code | `claude plugin marketplace add turbo-notify/agent-skills#v1` then `claude plugin install turbo-notify@turbo-notify` |
| Claude Desktop and Cowork | Customize, Plugins, Add, Add marketplace, Add from a repository: `turbo-notify/agent-skills`; then Discover, `turbo-notify`, Add |
| Codex | `codex plugin marketplace add turbo-notify/agent-skills --ref v1` then `codex plugin add turbo-notify@turbo-notify` |
| Antigravity | `agy plugin install https://github.com/turbo-notify/agent-skills` |

`#v1` and `--ref v1` keep you on 1.x: a release that breaks compatibility never arrives by itself.
Then connect the Turbo Notify MCP server to the same agent and ask it to "set up the Turbo Notify
virtual attendant".

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

- The Turbo Notify MCP server you connected, with your account or API key permissions.
- One folder on your computer (by default `~/.turbo-notify/attendant`) holding conversations, customer files and what you taught. Turbo Notify gets no copy.
- One network address besides Turbo Notify: `raw.githubusercontent.com/turbo-notify/agent-skills/main/latest.json`, read about once an hour (and again after 10 minutes if a check fails) for the version check. A failure changes nothing.
- Your other MCP servers only if you allow each one during setup.

## What it runs and what it sends

- It runs the `tn_assist` helper with `python3`. The helper uses only the standard library and installs nothing.
- In the unattended runner (Claude Code and Codex), it starts your own `claude` or `codex` command once per customer, in a separate process with no tools, to write that customer's answer. The process gets that customer's messages, their file and the business knowledge, and nothing else.
- The text of a customer's message goes to the model of the agent you use, under your account with that agent. It goes nowhere else.
- Answers go out through the Turbo Notify API with your API key, to the customer who wrote. A check before every send stops another customer's number or name, non-public text and credentials.
- The first answer of each chat tells the customer that it comes from an AI. The line is added by the helper, not by the model.

## Layout

```
.claude-plugin/        plugin and marketplace manifests (Claude Code, Codex)
plugin.json            manifest for Antigravity
agents/                the tool-less agent that answers a customer
skills/virtual-attendant/
  SKILL.md             the entry point the agent reads
  references/          one short file per topic (setup, tick, console, memory, privacy, ...)
  assets/templates/    interview templates (pt, en) and the memory policy
  scripts/tn_assist/   the helper: routing, memory, egress check, runner (Python 3.9+, no dependencies)
tools/release.py       version consistency check and sync
tests/                 unit, property and security tests (Poetry, development only)
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

Releases bump `VERSION`, `latest.json`, the three manifests, the helper and pyproject versions and `CHANGELOG.md` together (`tools/release.py sync`),
then move the `v1` tag.

## License

MIT. See [LICENSE](LICENSE).
