#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from mantis.app import MantisApp
from mantis.server import app as server_app


def load_cfg() -> dict:
    return json.loads((Path.home() / ".mantisai" / "config.json").read_text())


def build_model_cfg(cfg: dict, budget_usd: float) -> dict:
    return {
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "budget_usd": budget_usd,
        "deepseek_api_key": cfg.get("deepseek_api_key", ""),
        "openai_api_key": cfg.get("openai_api_key", ""),
        "anthropic_api_key": cfg.get("anthropic_api_key", ""),
        "alibaba_api_key": cfg.get("alibaba_api_key", ""),
        "minimax_api_key": cfg.get("minimax_api_key", ""),
    }


async def run_prompt(model_cfg: dict, cwd: Path, prompt: str, session_id: str) -> dict:
    app = MantisApp(model_cfg, project_dir=str(cwd), session_id=session_id)
    started = time.time()
    output = await app._run_chat(prompt)
    return {
        "response": output,
        "elapsed_s": round(time.time() - started, 2),
        "stats": app.last_stats,
    }


async def run_once(budget_usd: float) -> dict:
    cfg = load_cfg()
    model_cfg = build_model_cfg(cfg, budget_usd)
    root = Path(tempfile.mkdtemp(prefix="mantis-live-benchmark-"))
    result: dict[str, object] = {"root": str(root)}

    t1 = root / "task1"
    t1.mkdir()
    prompt1 = (
        f"Create {t1 / 'token_bucket.py'} implementing a TokenBucket class with methods "
        "__init__(capacity: int, refill_rate: float), allow(tokens: int = 1) -> bool, and available() -> float. "
        f"Also create {t1 / 'check_token_bucket.py'} with a few asserts that check that exact API. "
        "Do not run the check."
    )
    result["task1"] = await run_prompt(model_cfg, t1, prompt1, f"bench-task1-{int(time.time())}")
    token_bucket = t1 / "token_bucket.py"
    check_file = t1 / "check_token_bucket.py"
    task1_verify = {
        "token_bucket_exists": token_bucket.exists(),
        "check_exists": check_file.exists(),
    }
    if check_file.exists():
        proc = subprocess.run(["python", str(check_file)], cwd=t1, capture_output=True, text=True, timeout=60)
        task1_verify["check_code"] = proc.returncode
        task1_verify["check_out"] = proc.stdout[-500:]
        task1_verify["check_err"] = proc.stderr[-500:]
    result["task1_verify"] = task1_verify

    t2 = root / "task2"
    t2.mkdir()
    prompt2 = (
        f"Create {t2 / 'binary_search.py'} with a correct binary_search function and "
        f"{t2 / 'test_binary_search.py'} with pytest tests for found, missing, empty list, one element, duplicates, and boundaries. "
        "Do not run pytest."
    )
    result["task2"] = await run_prompt(model_cfg, t2, prompt2, f"bench-task2-{int(time.time())}")
    proc2 = subprocess.run(["python", "-m", "pytest", "-q"], cwd=t2, capture_output=True, text=True, timeout=120)
    result["task2_verify"] = {
        "pytest_code": proc2.returncode,
        "pytest_out": proc2.stdout[-800:],
        "pytest_err": proc2.stderr[-800:],
    }

    t3 = root / "task3"
    t3.mkdir()
    (t3 / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (t3 / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    prompt3 = f"Fix only {t3 / 'calc.py'} so the existing pytest test will pass. Do not run tests."
    result["task3"] = await run_prompt(model_cfg, t3, prompt3, f"bench-task3-{int(time.time())}")
    proc3 = subprocess.run(["python", "-m", "pytest", "-q"], cwd=t3, capture_output=True, text=True, timeout=120)
    result["task3_verify"] = {
        "pytest_code": proc3.returncode,
        "pytest_out": proc3.stdout[-800:],
        "pytest_err": proc3.stderr[-800:],
        "calc": (t3 / "calc.py").read_text(encoding="utf-8"),
    }

    t4 = root / "task4"
    t4.mkdir()
    edit_file = t4 / "edit_me.py"
    edit_file.write_text("def value():\n    return 1\n", encoding="utf-8")
    client = TestClient(server_app)
    session_id = f"bench-task4-{int(time.time())}"
    prompt4 = f"Read {edit_file}, change the return value from 1 to 2, verify the file was updated, and keep the final answer short."
    create_response = client.post("/api/jobs", json={"prompt": prompt4, "session_id": session_id})
    task4 = {"create_status": create_response.status_code, "create_body": create_response.json()}
    job_id = create_response.json().get("job_id") if create_response.status_code == 200 else None
    approval_id = None
    if job_id:
        final_job = None
        for _ in range(60):
            time.sleep(1)
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] == "awaiting_approval":
                approval_id = job.get("metadata", {}).get("approval_id")
                task4["paused_job"] = job
                break
            if job["status"] in {"done", "failed"}:
                final_job = job
                break
        if approval_id:
            approve = client.post(f"/api/approvals/{approval_id}/approve", json={"note": "benchmark approve"})
            task4["approve"] = {"status": approve.status_code, "body": approve.json()}
            for _ in range(60):
                time.sleep(1)
                job = client.get(f"/api/jobs/{job_id}").json()
                if job["status"] in {"done", "failed"}:
                    final_job = job
                    break
        task4["final_job"] = final_job or client.get(f"/api/jobs/{job_id}").json()
        task4["file_after"] = edit_file.read_text(encoding="utf-8")
    result["task4"] = task4

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Mantis live benchmark against real providers.")
    parser.add_argument("--loops", type=int, default=1)
    parser.add_argument("--budget", type=float, default=0.25)
    args = parser.parse_args()

    for index in range(args.loops):
        print(f"=== loop {index + 1}/{args.loops} ===")
        result = asyncio.run(run_once(args.budget))
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
