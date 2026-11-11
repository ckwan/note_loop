"""Fail when an eval score drops below a floor.

    python -m evals.gate                       # run the stub and check it
    python -m evals.gate --results run.json    # check a saved run from evals.run

Exit code 0 means every threshold held. 1 means at least one did not. CI runs this.
"""

import argparse
import json
import sys
from pathlib import Path

from app.llm import StubProvider

from .scoring import run_all, summarize

DEFAULT_THRESHOLDS = Path(__file__).parent / "thresholds.json"


def ratio(pair) -> float | None:
    done, total = pair
    return None if total == 0 else done / total


def metrics(summary: dict) -> dict[str, float | None]:
    """Every score as a number from 0 to 1, keyed like "draft.grounded"."""
    out: dict[str, float | None] = {}
    for name, pair in summary["draft_checks"].items():
        out[f"draft.{name}"] = ratio(pair)
    for name, pair in summary["fix_checks"].items():
        out[f"fix.{name}"] = ratio(pair)
    for key in (
        "catch_general", "catch_specialists", "catch_combined",
        "false_alarms_general", "false_alarms_specialists", "false_alarms_combined",
    ):
        out[f"review.{key}"] = ratio(summary[key])
    return out


def check(summary: dict, thresholds: dict) -> list[tuple[str, str, float | None, float, bool]]:
    """Returns (metric, rule, value, limit, passed) for every threshold."""
    values = metrics(summary)
    rows = []
    for rule, compare in (("min", lambda v, t: v >= t), ("max", lambda v, t: v <= t)):
        for name, limit in thresholds.get(rule, {}).items():
            if name not in values:
                raise SystemExit(f"Threshold for unknown metric {name!r}")
            value = values[name]
            # A metric with nothing to score cannot pass. Do not skip it silently.
            rows.append((name, rule, value, limit, value is not None and compare(value, limit)))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check eval scores against thresholds.")
    parser.add_argument("--results", help="A JSON file saved by evals.run. Default: run the stub.")
    parser.add_argument("--thresholds", default=str(DEFAULT_THRESHOLDS))
    args = parser.parse_args(argv)

    thresholds = json.loads(Path(args.thresholds).read_text())
    if args.results:
        summary = json.loads(Path(args.results).read_text())["summary"]
        label = args.results
    else:
        from dataclasses import asdict

        summary = asdict(summarize(*run_all(StubProvider())))
        label = "stub"

    rows = check(summary, thresholds)
    print(f"Eval gate for {label}\n")
    for name, rule, value, limit, passed in rows:
        shown = "n/a" if value is None else f"{value:.2f}"
        sign = ">=" if rule == "min" else "<="
        print(f"{'PASS' if passed else 'FAIL'}  {name:34} {shown:>5} {sign} {limit:.2f}")
    failed = [r for r in rows if not r[4]]
    print(f"\n{len(rows) - len(failed)} of {len(rows)} checks held.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
