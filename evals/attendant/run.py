"""Runs the evaluation on the real model, sequentially, and writes the records as it goes.

    poetry run python -m evals.attendant run            # all the sets
    poetry run python -m evals.attendant run --set language --label again
    poetry run python -m evals.attendant run --set refusal --label refusals
    poetry run python -m evals.attendant run --set offers --label offers
    poetry run python -m evals.attendant report --run evals/attendant/results/<folder>
    poetry run python -m evals.attendant rescore --run evals/attendant/results/<folder>

Each case gets a fresh workspace (one customer per context, as in production). A conversation
keeps one workspace for all its turns. Cases run one after the other, each with a timeout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from evals.attendant import cases as fixtures
from evals.attendant import harness, offers, refusal, report
from tn_assist import __version__ as helper_version

RESULTS = Path(__file__).resolve().parent / "results"
STEP_CASE = timedelta(minutes=1)
#: A conversation advances twelve minutes per turn: no catch-up alarm (30 min), no reply cap (6/h).
STEP_CONVERSATION = timedelta(minutes=12)
SETS = ("all", "no-invention", "controls", "language", "refusal", "offers")


def _line(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def run_case(
    case: fixtures.Case,
    shop: fixtures.Shop,
    *,
    timeout: float,
    attempts: int,
    model: Optional[str],
    env: dict[str, str],
) -> dict[str, Any]:
    with harness.temp_base() as base:
        recorder = harness.Recorder(timeout)
        session = harness.Session(
            shop, Path(base), recorder=recorder, model=model, env=env, step=STEP_CASE
        )
        turn = session.say(case.number, case.name, case.message, retries=attempts - 1)
    return report.build_case_record(case, shop.knowledge(), turn)


def run_conversation(
    scenario: fixtures.Conversation,
    shop: fixtures.Shop,
    *,
    timeout: float,
    attempts: int,
    model: Optional[str],
    env: dict[str, str],
    out: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with harness.temp_base() as base:
        recorder = harness.Recorder(timeout)
        session = harness.Session(
            shop, Path(base), recorder=recorder, model=model, env=env, step=STEP_CONVERSATION
        )
        for index, step in enumerate(scenario.turns, start=1):
            turn = session.say(scenario.number, scenario.name, step.message, retries=attempts - 1)
            record = report.build_turn_record(scenario, index, step, turn)
            records.append(record)
            _line(out / "turns.jsonl", record)
            print(f"  turn {index} ({step.lang}) {record['verdict']}", flush=True)
    return records


def run(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    shop = fixtures.load_shop()
    wanted = set(args.only or ())
    label = args.label or "run"
    out = Path(args.out) if args.out else RESULTS / f"{date.today().isoformat()}-{label}"
    out.mkdir(parents=True, exist_ok=True)
    for name in ("cases.jsonl", "turns.jsonl", "refusals.jsonl", "offers.jsonl"):
        (out / name).write_text("", encoding="utf-8")

    meta = {
        "date": date.today().isoformat(),
        "started": datetime.now().replace(microsecond=0).isoformat(),
        "harness": "claude-code",
        "role": "contact",
        "model_requested": args.model,
        "model_used": harness.detect_model(env, args.model),
        "cli": harness.cli_version(),
        "helper_version": helper_version,
        "timeout_s": args.timeout,
        "attempts": args.attempts,
        "set": args.set,
    }
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"model: {meta['model_used']}  cli: {meta['cli']}  out: {out}", flush=True)

    chosen = [
        c
        for c in fixtures.load_cases()
        if (not wanted or c.id in wanted)
        and (
            args.set == "all"
            or (args.set == "no-invention" and c.unsupported)
            or (args.set == "controls" and not c.unsupported)
        )
    ]
    for number, case in enumerate(chosen, start=1):
        record = run_case(
            case, shop, timeout=args.timeout, attempts=args.attempts, model=args.model, env=env
        )
        _line(out / "cases.jsonl", record)
        print(
            f"[{number}/{len(chosen)}] {case.id}: {record['verdict']} ({record['elapsed_s']}s)",
            flush=True,
        )
    if args.set in ("all", "language"):
        for scenario in fixtures.load_conversations():
            if wanted and scenario.id not in wanted:
                continue
            print(f"conversation {scenario.id}", flush=True)
            run_conversation(
                scenario, shop, timeout=args.timeout, attempts=args.attempts, model=args.model,
                env=env, out=out,
            )  # fmt: skip
    if args.set in ("all", "refusal"):
        for check in refusal.SCENARIOS:
            if wanted and check.id not in wanted:
                continue
            print(f"refusal {check.id}", flush=True)
            record = refusal.run_scenario(
                check, shop, timeout=args.timeout, model=args.model, env=env
            )
            _line(out / "refusals.jsonl", record)
            print(f"  {record['verdict']} {record['problems']}", flush=True)
    if args.set in ("all", "offers"):
        for probe in offers.SCENARIOS:
            if wanted and probe.id not in wanted:
                continue
            print(f"offer {probe.id}", flush=True)
            record = offers.run_scenario(
                probe, shop, timeout=args.timeout, model=args.model, env=env
            )
            _line(out / "offers.jsonl", record)
            print(f"  {record['verdict']} {record['problems']}", flush=True)
    summary = report.write_reports(out, meta)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.is_green(summary) else 1


def regenerate(args: argparse.Namespace) -> int:
    summary = report.write_reports(Path(args.run))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.is_green(summary) else 1


def rescore(args: argparse.Namespace) -> int:
    directory = Path(args.run)
    by_id = {c.id: c for c in fixtures.load_cases()}
    report.rescore(directory, fixtures.load_shop().knowledge(), by_id)
    summary = report.write_reports(directory)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.is_green(summary) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.attendant")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="run the cases on the real model")
    p.add_argument("--set", choices=SETS, default="all")
    p.add_argument("--only", nargs="+", help="case or conversation ids")
    p.add_argument("--model", help="passed to claude as --model (default: the CLI's own default)")
    p.add_argument("--timeout", type=float, default=240.0, help="seconds per model call")
    p.add_argument("--attempts", type=int, default=3, help="cycles to get an answer for a message")
    p.add_argument("--label", help="folder name suffix under results/")
    p.add_argument("--out", help="results folder (default results/<date>-<label>)")
    p.set_defaults(func=run)
    p = sub.add_parser("report", help="rebuild summary.json and review.txt from a results folder")
    p.add_argument("--run", required=True)
    p.set_defaults(func=regenerate)
    p = sub.add_parser("rescore", help="score a results folder again with the current checker")
    p.add_argument("--run", required=True)
    p.set_defaults(func=rescore)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
