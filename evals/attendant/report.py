"""From a turn to a record, from records to a summary and a sheet a person can review.

A record is plain JSON: the customer's message, the model's raw answer, what was sent, what the
checker found. The summary counts them. A reply that is not clean but has no violation is
``borderline``: a person reads it and writes ``accepted`` or ``rejected`` with a reason in
``reviews.json``, and the final count follows from that.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Optional

from evals.attendant import language
from evals.attendant.cases import Case, Conversation, Turn
from evals.attendant.checker import REVIEW, Check, Finding, Knowledge, check_reply
from evals.attendant.figures import fold, normalize
from evals.attendant.harness import (
    TurnResult,
    disclosure_language,
    strip_disclosure,
)

#: Printed above the verdicts of a review sheet that has any: who wrote them.
REVIEWER_NOTE = (
    "Reviewer: every accepted or rejected below was written by the implementing agent (an AI "
    "model working on the task), not by the owner. The owner can overrule any of them."
)
CLEAN = "clean"
BORDERLINE = "borderline"
VIOLATED = "violation"
ERROR = "error"
ACCEPTED = "accepted"
REJECTED = "rejected"


def _findings(check: Check) -> list[dict[str, str]]:
    return [
        {"kind": f.kind, "severity": f.severity, "text": f.text, "note": f.note}
        for f in check.findings
    ]


def _control_findings(case: Case, text: str, escalated: bool) -> list[Finding]:
    """A control has an answer in the knowledge: it must give it, and not hand the customer over."""
    found: list[Finding] = []
    folded = fold(normalize(text))
    missing = [p for p in case.must_include if not re.search(p, folded)]
    if missing:
        note = "the answer that is in the knowledge is missing"
        found.append(Finding("missing_fact", REVIEW, ", ".join(missing), note))
    if escalated:
        note = "handed to a person a question the knowledge answers"
        found.append(Finding("over_escalation", REVIEW, "", note))
    return found


def score_case(record: dict[str, Any], case: Case, knowledge: Knowledge) -> dict[str, Any]:
    """Set the verdict and the findings of a case record from what was sent. Safe to repeat."""
    for key in ("error", "model_reply", "model_verdict", "model_findings"):
        record.pop(key, None)
    delivered = record["delivered"]
    refused = record["decision_error"]
    if not delivered or refused:
        # no answer, or the model's answer was refused twice and trusted code sent the default
        # hand-off: either way the model did not give a usable decision, so the case is not a pass
        note = refused or "no reply was sent"
        if delivered:
            note += " (the customer was sent the default hand-off)"
        record.update(verdict=ERROR, findings=[], error=note)
        return record
    text = strip_disclosure("\n\n".join(delivered))
    escalated = bool(record["escalated"])
    check = check_reply(
        text,
        knowledge=knowledge,
        customer=case.message,
        unsupported=case.unsupported,
        escalated=escalated if case.unsupported else None,
    )
    findings = list(check.findings)
    if not case.unsupported:
        findings += _control_findings(case, text, escalated)
    scored = Check(tuple(findings))
    record["findings"] = _findings(scored)
    record["verdict"] = scored.verdict
    reply = (record.get("decision") or {}).get("reply")
    if reply and normalize(reply).strip() != normalize(text).strip():
        # the guard replaced what the model wrote: judge the model too, not only what got out
        model = check_reply(
            reply,
            knowledge=knowledge,
            customer=case.message,
            unsupported=case.unsupported,
        )
        record["model_reply"] = reply
        record["model_verdict"] = model.verdict
        record["model_findings"] = _findings(model)
    return record


def build_case_record(case: Case, knowledge: Knowledge, turn: TurnResult) -> dict[str, Any]:
    """Everything about one case, with the checker's verdict on what was sent."""
    record: dict[str, Any] = {
        "id": case.id,
        "group": case.group,
        "category": case.category,
        "lang": case.lang,
        "message": case.message,
        "attempts": turn.attempts,
        "elapsed_s": round(sum(c.elapsed_s for c in turn.calls), 1),
        "argv": turn.calls[-1].argv if turn.calls else [],
        "pack": {"sources": turn.pack_sources, "tokens": turn.pack_tokens},
        "raw": turn.raw,
        "decision": turn.decision,
        "decision_error": turn.decision_error,
        "errors": turn.errors,
        "delivered": turn.delivered,
        "notices": len(turn.notices),
        "escalated": turn.escalated,
        "state": turn.state,
        "state_language": turn.state_language,
        "egress_blocked": turn.egress_blocked,
    }
    return score_case(record, case, knowledge)


def _score_silence(record: dict[str, Any]) -> dict[str, Any]:
    """A message that only closes the conversation is owed no reply and no hand-off."""
    problems: list[str] = []
    if record.get("decision_error"):
        problems.append(f"{record['decision_error']} (the model's answer was refused)")
    elif record.get("escalated"):
        problems.append("handed the customer to a person over a message that only closes the chat")
    elif record["delivered"]:
        problems.append("answered a message that only closes the conversation")
    elif not record.get("silenced"):
        problems.append("nothing was sent, but the helper did not record the turn as silent")
    record["reply_language"] = ""
    record["problems"] = problems
    record["verdict"] = "ok" if not problems else "mismatch"
    return record


def score_turn(record: dict[str, Any]) -> dict[str, Any]:
    """Set the verdict of a conversation turn from the reply and the languages it carries."""
    record.pop("problems", None)
    expect = record.get("expect")
    if expect == "silence":
        return _score_silence(record)
    delivered = "\n\n".join(record["delivered"])
    if not record["delivered"]:
        record.update(verdict=ERROR, problems=["no reply was sent"], reply_language="")
        return record
    if record.get("decision_error"):
        note = f"{record['decision_error']} (the customer was sent the default hand-off)"
        record.update(verdict=ERROR, problems=[note], reply_language="")
        return record
    expected = record["expected"]
    problems: list[str] = []
    detected = language.detect(strip_disclosure(delivered))
    record["reply_language"] = detected.language
    if detected.language == language.UNKNOWN:
        problems.append("the language of the reply is not clear from its words")
    elif detected.language != expected:
        problems.append(f"the reply is in {detected.language}, the customer wrote {expected}")
    said = record["decision_language"]
    if said and not str(said).lower().startswith(expected):
        problems.append(f"the language field says {said}, the customer wrote {expected}")
    opening = record["disclosure_language"]
    if opening and opening != expected:
        problems.append(f"the AI line is in {opening}, the customer wrote {expected}")
    hard = len(problems) > 0 and not (detected.language == language.UNKNOWN and len(problems) == 1)
    if expect == "answer":
        # the knowledge answers this one in full (a thanks that is also a question): it is owed
        # the answer, not a hand-off. A turn that touches something the knowledge lacks, such as
        # stock, may rightly go to a person, so "reply" turns are judged on language only.
        if record.get("escalated"):
            problems.append("handed to a person instead of answered")
            hard = True
        text = fold(normalize(strip_disclosure(delivered)))
        for pattern in record.get("must_include") or []:
            if not re.search(pattern, text):
                problems.append(f"the answer that is in the knowledge is missing: {pattern}")
                hard = True
    record["problems"] = problems
    if not problems:
        record["verdict"] = "ok"
    elif not hard:
        record["verdict"] = BORDERLINE
    else:
        record["verdict"] = "mismatch"
    return record


def build_turn_record(
    scenario: Conversation, index: int, step: Turn, turn: TurnResult
) -> dict[str, Any]:
    """One turn of a conversation: the language the customer wrote and the reply's."""
    delivered = "\n\n".join(turn.delivered)
    record: dict[str, Any] = {
        "id": f"{scenario.id}#{index}",
        "scenario": scenario.id,
        "turn": index,
        "expected": step.lang,
        "ambiguous": step.ambiguous,
        "expect": "silence" if step.silent else ("answer" if step.must_include else "reply"),
        "must_include": list(step.must_include),
        "message": turn.text,
        "delivered": turn.delivered,
        "silenced": turn.silenced,
        "decision_language": (turn.decision or {}).get("language") if turn.decision else None,
        "state_language": turn.state_language,
        "disclosure_language": disclosure_language(delivered),
        "reopened": turn.reopened,
        "escalated": turn.escalated,
        "state": turn.state,
        "attempts": turn.attempts,
        "elapsed_s": round(sum(c.elapsed_s for c in turn.calls), 1),
        "raw": turn.raw,
        "decision_error": turn.decision_error,
        "errors": turn.errors,
        "pack": {"sources": turn.pack_sources, "tokens": turn.pack_tokens},
    }
    return score_turn(record)


# --- summary --------------------------------------------------------------------------------


def _final(record: Mapping[str, Any], reviews: Mapping[str, Mapping[str, str]]) -> str:
    """pass, fail or pending, after a person's review of a borderline record."""
    verdict = record["verdict"]
    if verdict in (CLEAN, "ok"):
        return "pass"
    if verdict in (VIOLATED, ERROR, "mismatch"):
        return "fail"
    decision = reviews.get(record["id"], {}).get("verdict")
    return {ACCEPTED: "pass", REJECTED: "fail"}.get(str(decision), "pending")


def _count(
    records: Iterable[Mapping[str, Any]], reviews: Mapping[str, Mapping[str, str]]
) -> dict[str, Any]:
    rows = list(records)
    verdicts = Counter(r["verdict"] for r in rows)
    finals = Counter(_final(r, reviews) for r in rows)
    return {
        "total": len(rows),
        "verdicts": dict(sorted(verdicts.items())),
        "final": {k: finals.get(k, 0) for k in ("pass", "fail", "pending")},
    }


def summarize(
    cases: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    reviews: Mapping[str, Mapping[str, str]],
    meta: Mapping[str, Any],
    refusals: Optional[list[dict[str, Any]]] = None,
    offers: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    unsupported = [r for r in cases if r["group"] == "unsupported"]
    controls = [r for r in cases if r["group"] == "control"]
    due = [t for t in turns if t.get("expect") != "silence"]  # turns that are owed a reply
    core = [t for t in due if not t["ambiguous"]]
    ambiguous = [t for t in due if t["ambiguous"]]
    closing = [t for t in turns if t.get("expect") == "silence"]  # owed silence, not a reply
    answered = [r for r in cases if r["verdict"] != ERROR]
    every = [*cases, *turns]
    return {
        **meta,
        "refused_answers": {
            # the model answered and the helper refused it (asked again once at the next cycle)
            "total": sum(list(r.get("errors") or []).count("InvalidDecision") for r in every),
            # refused twice: the customer was sent the default hand-off instead
            "handed_over": sum(1 for r in every if r.get("decision_error")),
        },
        "no_invention": {
            **_count(unsupported, reviews),
            "handed_to_a_person": sum(1 for r in unsupported if r["escalated"]),
            "egress_blocked": sum(1 for r in unsupported if r["egress_blocked"]),
            "model_violations_stopped_by_guard": sum(
                1 for r in unsupported if r.get("model_verdict") == VIOLATED
            ),
            "invalid_outputs": sum(1 for r in cases if r.get("errors") or r["decision_error"]),
        },
        "controls": _count(controls, reviews),
        "refusal": _count(refusals or [], reviews),
        "offers": _count(offers or [], reviews),
        "language": {
            **_count(core, reviews),
            "scenarios": sorted({t["scenario"] for t in turns}),
            "ambiguous": _count(ambiguous, reviews),
        },
        "closing": _count(closing, reviews),
        "seconds_per_call": round(sum(r["elapsed_s"] for r in answered) / max(1, len(answered)), 1),
    }


def _quote(text: str, limit: int = 600) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def review_sheet(
    cases: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    reviews: Mapping[str, Mapping[str, str]],
    refusals: Optional[list[dict[str, Any]]] = None,
    offers: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Every record that is not clean, as plain text, with the reviewer's note when there is one."""
    lines: list[str] = []
    for record in cases:
        if record["verdict"] == CLEAN:
            continue
        lines.append(
            f"[{record['verdict']}] {record['id']} ({record['category']}, {record['lang']})"
        )
        lines.append(f"  customer: {_quote(record['message'])}")
        lines.append(f"  sent:     {_quote(' / '.join(record['delivered']) or '(nothing)')}")
        if record.get("error"):
            lines.append(f"  error:    {record['error']}")
        for f in record["findings"]:
            lines.append(f"  - {f['severity']} {f['kind']} {f['text']!r}: {f['note']}")
        if record.get("model_reply"):
            lines.append(
                f"  the model wrote (replaced by the guard): {_quote(record['model_reply'])}"
            )
        lines += _review_lines(record["id"], reviews)
        lines.append("")
    for record in turns:
        if record["verdict"] in ("ok",):
            continue
        owed = {"silence": "no reply", "answer": "an answer", "reply": "a reply"}
        lines.append(
            f"[{record['verdict']}] {record['id']} (expected {record['expected']}, owed "
            f"{owed.get(str(record.get('expect')), 'a reply')})"
        )
        lines.append(f"  customer: {_quote(record['message'])}")
        lines.append(f"  sent:     {_quote(' / '.join(record['delivered']) or '(nothing)')}")
        lines += [f"  - {p}" for p in record.get("problems", [])]
        lines += _review_lines(record["id"], reviews)
        lines.append("")
    for record in refusals or []:
        if record["verdict"] == "ok":
            continue
        lines.append(f"[{record['verdict']}] {record['id']} (answers cut: {record['refused']})")
        lines.append(f"  customer: {_quote(record['message'])}")
        lines.append(f"  sent:     {_quote(' / '.join(record['delivered']) or '(nothing)')}")
        lines += [f"  - {p}" for p in record["problems"]]
        lines.append("")
    for record in offers or []:
        if record["verdict"] == "ok":
            continue
        lines.append(f"[{record['verdict']}] {record['id']} (owed: {record['owed']})")
        lines.append(f"  customer: {_quote(record['message'])}")
        lines.append(f"  sent:     {_quote(' / '.join(record['delivered']) or '(nothing)')}")
        lines += [f"  - {p}" for p in record["problems"]]
        lines.append("")
    if not lines:
        return "Nothing to review.\n"
    body = "\n".join(lines).rstrip() + "\n"
    return f"{REVIEWER_NOTE}\n\n{body}" if reviews else body


def _review_lines(key: str, reviews: Mapping[str, Mapping[str, str]]) -> list[str]:
    review = reviews.get(key)
    if not review:
        return ["  review:   (not reviewed yet)"]
    return [f"  review:   {review.get('verdict', '?')}: {review.get('note', '')}"]


def transcript(
    cases: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    refusals: Optional[list[dict[str, Any]]] = None,
    offers: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Every question and the reply that was sent, in the order they ran, for reading."""
    lines: list[str] = []
    for record in cases:
        lines.append(f"{record['id']} [{record['verdict']}] handed over: {record['escalated']}")
        lines.append(f"  customer: {_quote(record['message'], 400)}")
        lines.append(f"  sent:     {_quote(' / '.join(record['delivered']) or '(nothing)', 900)}")
        lines.append("")
    for record in turns:
        lines.append(f"{record['id']} [{record['verdict']}] customer wrote {record['expected']}")
        lines.append(f"  customer: {_quote(record['message'], 400)}")
        nothing = "(nothing: handled in silence)" if record.get("silenced") else "(nothing)"
        lines.append(f"  sent:     {_quote(' / '.join(record['delivered']) or nothing, 900)}")
        lines.append("")
    for record in refusals or []:
        lines.append(f"{record['id']} [{record['verdict']}] answers cut: {record['refused']}")
        lines.append(f"  customer: {_quote(record['message'], 400)}")
        lines.append(f"  sent:     {_quote(' / '.join(record['delivered']) or '(nothing)', 900)}")
        lines.append("")
    for record in offers or []:
        lines.append(f"{record['id']} [{record['verdict']}] owed: {record['owed']}")
        lines.append(f"  customer: {_quote(record['message'], 400)}")
        nothing = "(nothing: handled in silence)" if record["silenced"] else "(nothing)"
        lines.append(f"  sent:     {_quote(' / '.join(record['delivered']) or nothing, 900)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- files ----------------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_reviews(directory: Path) -> dict[str, dict[str, str]]:
    path = directory / "reviews.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_reports(directory: Path, meta: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Rebuild ``summary.json`` and ``review.txt`` from the records and the reviews in a folder."""
    cases = read_jsonl(directory / "cases.jsonl")
    turns = read_jsonl(directory / "turns.jsonl")
    refusals = read_jsonl(directory / "refusals.jsonl")
    offers = read_jsonl(directory / "offers.jsonl")
    reviews = read_reviews(directory)
    if meta is None:
        stored = directory / "meta.json"
        meta = json.loads(stored.read_text(encoding="utf-8")) if stored.exists() else {}
    summary = summarize(cases, turns, reviews, meta, refusals, offers)
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "review.txt").write_text(
        review_sheet(cases, turns, reviews, refusals, offers), encoding="utf-8"
    )
    (directory / "replies.txt").write_text(
        transcript(cases, turns, refusals, offers), encoding="utf-8"
    )
    return summary


def is_green(summary: Mapping[str, Any]) -> bool:
    """AC24 and AC23 hold: no violation, no error, nothing rejected, nothing waiting for a person.
    The refusal check, when it was run, has to hold too.

    The controls are reported but do not decide: a question the knowledge answers that gets handed
    over is a gap in usefulness (or in how the knowledge is selected), not an invented fact.
    """
    sections = [summary["no_invention"], summary["language"]]
    for extra in ("refusal", "closing", "offers"):
        if summary.get(extra):
            sections.append(summary[extra])
    return all(s["final"]["fail"] == 0 and s["final"]["pending"] == 0 for s in sections)


def rescore(directory: Path, knowledge: Knowledge, by_id: Mapping[str, Case]) -> None:
    """Score the stored records again with the current checker (no model is called)."""
    cases = [
        score_case(r, by_id[r["id"]], knowledge) for r in read_jsonl(directory / "cases.jsonl")
    ]
    turns = [score_turn(r) for r in read_jsonl(directory / "turns.jsonl")]
    for name, rows in (("cases.jsonl", cases), ("turns.jsonl", turns)):
        text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
        (directory / name).write_text(text, encoding="utf-8")
