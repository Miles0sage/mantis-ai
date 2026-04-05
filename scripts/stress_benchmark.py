#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_SCENARIO_PATH = Path(__file__).with_name("scenario_benchmark.py")
_SPEC = importlib.util.spec_from_file_location("mantis_scenario_benchmark", _SCENARIO_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Unable to load scenario benchmark module from {_SCENARIO_PATH}")
sb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sb)


FAST_SCENARIOS = [
    sb.scenario_python_multifile_pass,
    sb.scenario_python_interface_fail,
    sb.scenario_javascript_multifile_pass,
    sb.scenario_python_guardrail_blocks_raw_edits,
    sb.scenario_background_job_complete,
    sb.scenario_model_approval_resume,
]


async def run_once() -> dict:
    root = Path(tempfile.mkdtemp(prefix="mantis-stress-benchmark-"))
    results = []
    for scenario in FAST_SCENARIOS:
        results.append(await scenario(root))
    passed = sum(1 for result in results if result["passed"])
    return {
        "root": str(root),
        "pass_count": passed,
        "total": len(results),
        "pass_rate": round(passed / len(results), 3),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fast repeated Mantis stability benchmark.")
    parser.add_argument("--loops", type=int, default=20)
    parser.add_argument("--report-dir", type=str, default=".omc/state/stress-benchmark-reports")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    all_results = []
    for index in range(args.loops):
        result = asyncio.run(run_once())
        print(f"=== loop {index + 1}/{args.loops} ===")
        print(json.dumps(result, indent=2))
        summaries.append(result["pass_rate"])
        all_results.append(result)

    summary = {
        "loops": args.loops,
        "average_pass_rate": round(sum(summaries) / len(summaries), 3),
        "min_pass_rate": min(summaries),
        "max_pass_rate": max(summaries),
        "all_green": all(rate == 1.0 for rate in summaries),
    }
    print(json.dumps(summary, indent=2))

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (report_dir / f"stress-benchmark-{timestamp}.json").write_text(
        json.dumps({"summary": summary, "loops": all_results}, indent=2) + "\n",
        encoding="utf-8",
    )
    (report_dir / "latest-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
