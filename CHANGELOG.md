# Changelog

## 0.3.1

- Tooling: `python3 tools/openai_package.py` builds the archive that the OpenAI plugin directory takes: the manifest with the listing, review cases and publication data (from `tools/openai_listing.json`, checked against the portal's limits), the skill, the icon and the MCP server file, without the files and the lines that belong to another vendor's runtime. It only writes a file in `dist/`; nothing is sent anywhere.
- Docs: the plugin `homepage` points to the current page of the skills guide, which no longer goes through a redirect.
- Fix: when a send is refused with `number_not_connected`, the attendant now reads the number first. A number that is `reconnecting`, `disconnected` or `inactive` is waited for or brought back with `connect_number` and tried again next cycle, as before. A number that is `pending`, `failed` or `revoked` cannot be fixed by the assistant: it keeps the delivery, does not call `connect_number`, and tells the person who runs the assistant, in the conversation, that the number has to be linked in the Numbers page of the dashboard. Before, every such refusal was read as "reconnecting" and retried until the answer expired.
- Docs: the install commands for Claude Code and Codex no longer pin the `v1` tag. The default branch only receives releases, so an install from it already follows the latest stable version. The `v1` tag is still moved at each release for anyone who wants to stay on 1.x.

## 0.3.0

- The plugin now declares the Turbo Notify MCP server itself (`https://mcp.turbonotify.com`), in the file each family of agents reads: `.mcp.json` (Claude Code, Claude Desktop and Cowork), `mcp.json` (the Agent Plugins format that Copilot and Cursor read, and the OpenAI directory upload; Codex reads `.mcp.json`) and `mcp_config.json` (Antigravity). Installing the plugin is enough to have the tools; you only sign in. The files hold no key and no header, and `tools/release.py check` fails if one points elsewhere, has a field it does not list or goes missing. A server you added by hand under the same name or address keeps priority over the one the plugin declares, so a copy added with an API key stays as it is.
- A customer who only says thanks, "ok" or sends a thumbs up now gets no answer and is not handed to a person. Before, a model that had nothing to add could only send an empty reply, which the helper reads as a hand-off, so the customer was parked until someone resumed. The decision has a new optional field, `no_reply_needed`: the model has to set it, and the helper honours it only when every message still waiting for an answer was read in full and is nothing but a thanks or an acknowledgement (at most 60 characters and six words, no question mark, no number, all words from a fixed list in Portuguese and English). On anything else, "ok, e o frete?" included, the customer is handed to a person as before, and the administrators are told why. A silent turn is finished like any other, makes no draft, does not count as a reply and is never a refusal. A reply of `""` now counts as a reply of null, so silence can only be asked for in words. `tn_assist apply` reports `silent`.
- The second try after a refused answer says which rule the first one broke: one line at the end of the new pack (the answer was not valid JSON, it had a field that is not in the format, a value was of the wrong kind), written by the helper from fixed words and holding nothing the customer or the model wrote. In a session, `tn_assist apply` remembers the rule and the next `tn_assist pack` carries the line.
- Fix: "how much" and "how many" count as questions about a price and a quantity, and "sell" as a question about the products, so "How much is the lavender candle?" finds the catalog. Before, an English customer asking for a product by its English name was handed to a person because the file with the price was not in the pack.
- Fix: a question the knowledge answers now reaches the file that answers it. The files that go into a customer's turn (besides company, tone and hand-off) are chosen by the words of the new message and, with less weight, the last lines of the conversation. Filler in Portuguese and English ("como", "posso", "the", "can") counts for nothing, a word matches by its first four letters ("pagar" finds "pagamento"), a small vocabulary of shop words in both languages ties together what a customer and an owner say differently ("reembolso" and "devolução", "pay" and "pagamento"), and the three extra files are picked after the always-present ones are set aside, in a fixed order. Before, "Como posso pagar?" found the tone and FAQ files through "como" and "posso" and not the file with the payment methods, so a customer was handed to a person for an answer the business had written, and in one conversation the assistant took back a true "we accept Pix" two messages later.
- Fix: every reason to hand a customer to a person is now in the rules the model reads on each turn, not only in the reference page: the answer is not in the knowledge (price, stock, deadline, promise, policy), the customer asks for a person, is upset, asks for something outside the business, raises a payment problem, a refund or exchange, a complaint or a legal matter, asks for a promise or a written confirmation, two replies in a row did not help, or sends only a file with nothing readable. A plain question about a written rule ("how long is the exchange window?") is still answered from the knowledge. Before, "if I don't like it you promise to exchange it, confirm in writing" was answered from the policy with no hand-off in every run on the large model.
- Fix: an answer that the helper refuses (not JSON, a field it does not know) no longer holds the conversation for half an hour. The model process failing to run keeps the old wait (1, 5 and 25 minutes, then 2 hours), because that is an outage. An answer that was received and refused is asked for once more at the next cycle, and a second refusal in a row sends the customer the same hand-off a missing reply gets (one message, a notice to the people, the assistant silent until a person resumes). The hand-off is in the language the customer wrote in: when no earlier answer recorded it, the helper reads it from the words of the new messages (Portuguese or English) and otherwise keeps the language it already had. An administrator or attendant whose message could not be answered gets a short reply asking them to try again.
- Fix: the hand-off and the opt-out confirmation that the helper writes when the model leaves them out, and the AI line, follow the language of the customer instead of the language of the workspace. An English customer of a Portuguese business no longer gets the hand-off in Portuguese, and a Portuguese customer of an English business no longer gets the AI line in English. The model is also told to answer in the language of the new message even when the knowledge is written in another.
- Fix: an answer is no longer thrown away when the model names the language in words ("English") instead of giving its code. The name is read as the code (`en`), a language field that cannot be read is ignored, and the model is told to give the code. Before, the whole answer was rejected and retried with a pause, which also held back the next messages of the conversation.
- A reply states a price, an hour or a deadline only as the business's knowledge writes it. It no longer multiplies a listed price into a total, works out a date or a weekday, says what day it is, or concludes from a missing entry that the business does not do something. A holiday or a special day that the knowledge does not cover goes to a person. Found by running 40 questions with no answer in the knowledge on a real model.
- The first answer of each chat now says it comes from an AI, in the customer's language (Portuguese or English). Trusted code adds one line before the answer, so a decision cannot leave it out. A chat is a stretch of conversation: the line comes back after a day without an answer from the assistant. The confirmation of an opt-out does not carry it.

## 0.2.4

- Docs and update notice: the Claude Desktop and Cowork steps now use the real menu names (Customize, Plugins, Add, Add marketplace, Add from a repository, then Discover and Add) and no longer mention an update button that Claude does not document.

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
