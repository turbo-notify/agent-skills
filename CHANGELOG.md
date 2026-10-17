# Changelog

## 0.2.3

- Fix: a WhatsApp display name that is a word every reply uses (a greeting, a thank you, a plan or a weekday) no longer blocks other customers' replies, and the marker that strips a forged `(platform)` provenance from a fact is covered like the others.

## 0.2.2

- Fix: the name WhatsApp shows for a customer is kept as a `platform` fact, so a one-word name of another customer is still kept out of replies and a fact the model infers from a customer's words can neither replace it nor forge a name.

## 0.2.1

- Fix: a conversation that an administrator paused after a person had taken it over was handed back by the automatic resume; only a takeover is handed back now.

## 0.2.0

- A conversation that a person took over can come back to the virtual attendant by itself: set `auto_resume_after_minutes` (off by default, minimum 5) in `memory-policy.json`. The time counts from the last message a person wrote, and the administrators and the number's own chat get a notice.
- `/politica` shows the new setting.
- Setup now writes `memory-policy.json`, knowledge files are private to the user, and automatic mode is refused where a tool-less customer turn is not possible (Claude Desktop, Cowork, Antigravity).
- The runner holds an answer it cannot deliver yet (a key problem, a reconnecting number, an unknown limit) instead of dropping it, backs off failed attempts and survives a failing cycle.

## 0.1.0

First release.

- `virtual-attendant`: a WhatsApp attendant for businesses that sell products or services.
  Answers direct chats in the customer's language, keeps a memory per customer with automatic
  compaction, calls a person when it should, and takes commands from administrators and
  attendants on WhatsApp (Portuguese and English).
- Assisted mode (a person approves each draft) and automatic mode.
- Unattended runner for Claude Code and Codex; in-session, assisted use in Claude Desktop, Cowork
  and Antigravity.
- Version check on every cycle, with the update command for the agent in use.
