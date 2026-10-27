"""The live gate: the same sets as ``python -m evals.attendant run``, as pytest tests.

Skipped unless you ask for a real model, so a plain ``pytest`` (which only looks in ``tests/``)
never starts one, and ``pytest evals`` on a machine without the ``claude`` command just skips.

    TN_EVALS_LIVE=1 poetry run pytest evals -p no:cacheprovider

It fails on a violation or an error. A borderline reply is not a failure here: a person reads those
(``review.txt``) and writes the verdict into ``reviews.json`` of a results folder.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from evals.attendant import run

pytestmark = pytest.mark.skipif(
    os.environ.get("TN_EVALS_LIVE") != "1" or shutil.which("claude") is None,
    reason="set TN_EVALS_LIVE=1 and log in to the claude command to run on a real model",
)


def _run(tmp_path: Path, which: str) -> dict[str, Any]:
    out = tmp_path / which
    run.main(["run", "--set", which, "--out", str(out)])
    summary: dict[str, Any] = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    return summary


def test_no_reply_invents_a_price_a_date_a_time_a_quantity_a_discount_or_a_promise(
    tmp_path: Path,
) -> None:
    summary = _run(tmp_path, "no-invention")["no_invention"]
    assert summary["total"] == 40
    assert summary["verdicts"].get("violation", 0) == 0, summary
    assert summary["verdicts"].get("error", 0) == 0, summary


def test_the_controls_are_answered_without_inventing_anything(tmp_path: Path) -> None:
    summary = _run(tmp_path, "controls")["controls"]
    assert summary["verdicts"].get("violation", 0) == 0, summary
    assert summary["verdicts"].get("error", 0) == 0, summary


def test_the_reply_follows_the_language_of_the_last_message(tmp_path: Path) -> None:
    summary = _run(tmp_path, "language")["language"]
    assert summary["verdicts"].get("mismatch", 0) == 0, summary
    assert summary["verdicts"].get("error", 0) == 0, summary


def test_a_refused_answer_is_asked_again_once_and_then_the_customer_goes_to_a_person(
    tmp_path: Path,
) -> None:
    summary = _run(tmp_path, "refusal")["refusal"]
    assert summary["total"] == 3 and summary["verdicts"].get("ok", 0) == 3, summary


def test_an_ok_that_accepts_an_offer_is_never_left_without_an_answer(tmp_path: Path) -> None:
    summary = _run(tmp_path, "offers")["offers"]
    assert summary["total"] == 10 and summary["verdicts"].get("ok", 0) == 10, summary
