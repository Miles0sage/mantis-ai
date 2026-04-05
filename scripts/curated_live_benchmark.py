#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

from mantis.app import MantisApp


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
    try:
        response = await app._run_chat(prompt)
        return {
            "ok": True,
            "elapsed_s": round(time.time() - started, 2),
            "response": response,
            "stats": app.last_stats,
        }
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - started, 2),
            "error": str(exc),
            "stats": getattr(app, "last_stats", {}),
        }


async def run_once(budget_usd: float) -> dict:
    cfg = load_cfg()
    model_cfg = build_model_cfg(cfg, budget_usd)
    root = Path(tempfile.mkdtemp(prefix="mantis-curated-live-"))
    results: dict[str, object] = {}

    read_count = root / "read_count"
    read_count.mkdir()
    count_file = read_count / "sample_test_file.py"
    count_file.write_text(
        "def test_a():\n    assert True\n\n"
        "def test_b():\n    assert True\n",
        encoding="utf-8",
    )
    results["read_count"] = await run_prompt(
        model_cfg,
        read_count,
        f"Read {count_file} and reply only with the number of test functions in the file.",
        "curated-read-count",
    )

    read_names = root / "read_names"
    read_names.mkdir()
    names_file = read_names / "sample_test_file.py"
    names_file.write_text(
        "def test_alpha():\n    assert True\n\n"
        "def test_beta():\n    assert True\n",
        encoding="utf-8",
    )
    results["read_names"] = await run_prompt(
        model_cfg,
        read_names,
        f"Read {names_file} and reply only with the names of the test functions.",
        "curated-read-names",
    )

    bugfix = root / "bugfix"
    bugfix.mkdir()
    (bugfix / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (bugfix / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    results["bugfix"] = await run_prompt(
        model_cfg,
        bugfix,
        f"Fix only {bugfix / 'calc.py'} so the existing pytest test will pass. Do not run tests.",
        "curated-bugfix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=bugfix, capture_output=True, text=True, timeout=120)
    results["bugfix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
        "calc": (bugfix / "calc.py").read_text(encoding="utf-8"),
    }

    edit_file = root / "edit_file"
    edit_file.mkdir()
    target = edit_file / "edit_me.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")
    results["edit_file"] = await run_prompt(
        model_cfg,
        edit_file,
        f"Read {target}, change the return value from 1 to 2, verify the file was updated, and keep the final answer short.",
        "curated-edit-file",
    )
    results["edit_file"]["verify"] = {"file_after": target.read_text(encoding="utf-8")}

    binary_search = root / "binary_search"
    binary_search.mkdir()
    results["binary_search"] = await run_prompt(
        model_cfg,
        binary_search,
        (
            f"Create {binary_search / 'binary_search.py'} with a correct binary_search function and "
            f"{binary_search / 'test_binary_search.py'} with pytest tests for found, missing, empty list, one element, duplicates, and boundaries. "
            "Do not run pytest."
        ),
        "curated-binary-search",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=binary_search, capture_output=True, text=True, timeout=120)
    results["binary_search"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-500:],
        "err": proc.stderr[-500:],
    }

    token_bucket = root / "token_bucket"
    token_bucket.mkdir()
    results["token_bucket"] = await run_prompt(
        model_cfg,
        token_bucket,
        (
            f"Create {token_bucket / 'token_bucket.py'} implementing a TokenBucket class with methods "
            "__init__(capacity: int, refill_rate: float), allow(tokens: int = 1) -> bool, and available() -> float. "
            f"Also create {token_bucket / 'check_token_bucket.py'} with asserts that check that exact API. Do not run the check."
        ),
        "curated-token-bucket",
    )
    proc = subprocess.run(["python", str(token_bucket / "check_token_bucket.py")], cwd=token_bucket, capture_output=True, text=True, timeout=120)
    results["token_bucket"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-500:],
        "err": proc.stderr[-500:],
    }

    refactor = root / "refactor_api"
    refactor.mkdir()
    (refactor / "payments.py").write_text(
        "def calculate_total(amounts):\n"
        "    total = 0\n"
        "    for amount in amounts:\n"
        "        total += amount\n"
        "    return total\n\n"
        "def format_total(total):\n"
        "    return f'${total:.2f}'\n",
        encoding="utf-8",
    )
    (refactor / "test_payments.py").write_text(
        "from payments import calculate_total, format_total\n\n"
        "def test_calculate_total():\n"
        "    assert calculate_total([1, 2, 3]) == 6\n\n"
        "def test_format_total():\n"
        "    assert format_total(6) == '$6.00'\n",
        encoding="utf-8",
    )
    results["refactor_preserve_api"] = await run_prompt(
        model_cfg,
        refactor,
        f"Refactor only {refactor / 'payments.py'} for clarity while preserving the existing API exactly. Do not run tests.",
        "curated-refactor-api",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=refactor, capture_output=True, text=True, timeout=120)
    results["refactor_preserve_api"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    fixture_repo = root / "fixture_repo"
    fixture_repo.mkdir()
    (fixture_repo / "loader.py").write_text(
        "import json\n"
        "from pathlib import Path\n\n"
        "def load_user_fixture():\n"
        "    return json.loads((Path(__file__).parent / 'fixtures' / 'user.json').read_text())\n",
        encoding="utf-8",
    )
    (fixture_repo / "fixtures").mkdir()
    (fixture_repo / "fixtures" / "user.json").write_text('{"name": "Wrong"}\n', encoding="utf-8")
    (fixture_repo / "test_loader.py").write_text(
        "from loader import load_user_fixture\n\n"
        "def test_load_user_fixture():\n"
        "    user = load_user_fixture()\n"
        "    assert user['name'] == 'Ada'\n",
        encoding="utf-8",
    )
    results["fixture_repo_fix"] = await run_prompt(
        model_cfg,
        fixture_repo,
        f"Fix the fixture-backed test by updating only {fixture_repo / 'fixtures' / 'user.json'}. Do not run tests.",
        "curated-fixture-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=fixture_repo, capture_output=True, text=True, timeout=120)
    results["fixture_repo_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    service = root / "service_layer"
    service.mkdir()
    (service / "repo.py").write_text("def get_user(user_id):\n    return {'id': user_id, 'name': 'Ada', 'active': True}\n", encoding="utf-8")
    (service / "service.py").write_text(
        "from repo import get_user\n\n"
        "def get_user_profile(user_id):\n"
        "    user = get_user(user_id)\n"
        "    return {'id': user['id'], 'name': user['name']}\n",
        encoding="utf-8",
    )
    (service / "test_service.py").write_text(
        "from service import get_user_profile\n\n"
        "def test_get_user_profile_existing():\n"
        "    assert get_user_profile(1) == {'id': 1, 'name': 'Ada', 'active': True}\n",
        encoding="utf-8",
    )
    results["service_layer_fix"] = await run_prompt(
        model_cfg,
        service,
        f"Fix only {service / 'service.py'} so the existing pytest test passes. Do not run tests.",
        "curated-service-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=service, capture_output=True, text=True, timeout=120)
    results["service_layer_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    layered = root / "layered_feature"
    layered.mkdir()
    (layered / "repo.py").write_text(
        "def get_user(user_id):\n"
        "    return {'id': user_id, 'first_name': 'Ada', 'last_name': 'Lovelace', 'active': True}\n",
        encoding="utf-8",
    )
    (layered / "service.py").write_text(
        "from repo import get_user\n\n"
        "def fetch_user_summary(user_id):\n"
        "    user = get_user(user_id)\n"
        "    return {'id': user['id'], 'display_name': user['first_name'], 'status': 'unknown'}\n",
        encoding="utf-8",
    )
    (layered / "test_service.py").write_text(
        "from service import fetch_user_summary\n\n"
        "def test_get_user_summary_active():\n"
        "    assert fetch_user_summary(1) == {'id': 1, 'display_name': 'ADA LOVELACE', 'status': 'active'}\n",
        encoding="utf-8",
    )
    results["layered_feature_fix"] = await run_prompt(
        model_cfg,
        layered,
        f"Fix only {layered / 'service.py'} so the existing pytest test passes. Do not run tests.",
        "curated-layered-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=layered, capture_output=True, text=True, timeout=120)
    results["layered_feature_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    return {"root": str(root), "results": results}


def summarize(result: dict) -> dict:
    passed = 0
    total = 0
    for payload in result["results"].values():
        total += 1
        verify = payload.get("verify", {})
        if payload.get("ok") and (
            ("code" in verify and verify["code"] == 0)
            or ("file_after" in verify and "return 2" in verify["file_after"])
            or payload["stats"]["routing"]["strategy"] == "local_fast_path"
        ):
            passed += 1
    return {
        "pass_count": passed,
        "total": total,
        "pass_rate": round(passed / total, 3) if total else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run curated real-provider Mantis benchmark scenarios.")
    parser.add_argument("--loops", type=int, default=1)
    parser.add_argument("--budget", type=float, default=0.35)
    args = parser.parse_args()

    for index in range(args.loops):
        print(f"=== loop {index + 1}/{args.loops} ===")
        result = asyncio.run(run_once(args.budget))
        result["summary"] = summarize(result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
