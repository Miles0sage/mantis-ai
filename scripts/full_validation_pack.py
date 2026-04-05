#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_step(name: str, cmd: list[str], cwd: Path) -> dict:
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return {
        "name": name,
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "elapsed_s": round(time.time() - started, 2),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Mantis validation pack.")
    parser.add_argument("--stress-loops", type=int, default=50)
    parser.add_argument("--curated-loops", type=int, default=1)
    parser.add_argument("--curated-budget", type=float, default=0.35)
    parser.add_argument("--curated-timeout", type=float, default=120.0)
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-scenario", action="store_true")
    parser.add_argument("--skip-stress", action="store_true")
    parser.add_argument("--skip-curated", action="store_true")
    args = parser.parse_args()

    steps: list[tuple[str, list[str]]] = []
    if not args.skip_pytest:
        steps.append(("pytest", ["pytest", "-q"]))
    if not args.skip_scenario:
        steps.append(("scenario", ["python", "scripts/scenario_benchmark.py", "--include-server"]))
    if not args.skip_stress:
        steps.append(("stress", ["python", "scripts/stress_benchmark.py", "--loops", str(args.stress_loops)]))
    if not args.skip_curated:
        steps.append(
            (
                "curated",
                [
                    "python",
                    "-u",
                    "scripts/curated_live_benchmark.py",
                    "--loops",
                    str(args.curated_loops),
                    "--budget",
                    str(args.curated_budget),
                    "--timeout",
                    str(args.curated_timeout),
                ],
            )
        )

    results: list[dict] = []
    overall_ok = True
    for name, cmd in steps:
        print(f"[start] {name}: {' '.join(cmd)}", flush=True)
        result = run_step(name, cmd, REPO_ROOT)
        print(
            f"[done] {name}: rc={result['returncode']} elapsed={result['elapsed_s']}s",
            flush=True,
        )
        results.append(result)
        if result["returncode"] != 0:
            overall_ok = False

    print(
        json.dumps(
            {
                "ok": overall_ok,
                "repo_root": str(REPO_ROOT),
                "results": results,
            },
            indent=2,
        )
    )
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
