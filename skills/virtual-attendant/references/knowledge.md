# Knowledge

One folder per number: `knowledge/`. `INDEX.md` lists every file in one line, `- path: what it covers`. Files under `knowledge/internal/` are for the team and are never given to a customer's turn, and their wording cannot leave in a reply. Everything else is public: customers may be told it.

## Suggested files

`company.md` (who the business is, hours, address), `offers.md`, `pricing.md`, `faq.md`, `policies.md` (refunds, delivery, warranty), `tone.md` (how to talk), `handoff.md` (when to call a person), `products/<name>.md`. The templates in `assets/templates/<lang>/` have the questions to ask for each.

## Writing it

- The configurator: `tn_assist kb-write --path pricing.md --visibility public|internal --description "..." --from-file <file>`.
- Administrators, later, from WhatsApp: `/ensinar <topic>`, then plain messages, then `/pronto`, then `/publico` or `/interno`. The text is stored word for word.

## Rules

- Write the person's words, not your guess. Do not fill gaps. A missing price is a reason to escalate, not to invent.
- Ask "is this for customers?" for every topic. A margin, a supplier cost or an internal rule is `internal`.
- Prices have a date or a condition when the person gives one ("until 31/10", "for new customers").
- One topic per file keeps the pack small. The pack carries the core files and the few that match the customer's message.
- A contradiction (two files give different prices for the same thing) is worth telling an administrator about.

## Checking

`/conhecimento` lists the files with who can read each. `/conhecimento <topic>` shows one.
