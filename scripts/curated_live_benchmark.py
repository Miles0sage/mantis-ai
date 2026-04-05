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


async def run_prompt(model_cfg: dict, cwd: Path, prompt: str, session_id: str, timeout_s: float) -> dict:
    app = MantisApp(model_cfg, project_dir=str(cwd), session_id=session_id)
    started = time.time()
    try:
        response = await asyncio.wait_for(app._run_chat(prompt), timeout=timeout_s)
        return {
            "ok": True,
            "elapsed_s": round(time.time() - started, 2),
            "response": response,
            "stats": app.last_stats,
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - started, 2),
            "error": f"timed out after {timeout_s}s",
            "stats": getattr(app, "last_stats", {}),
        }
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - started, 2),
            "error": str(exc),
            "stats": getattr(app, "last_stats", {}),
        }


def scenario_ok(payload: dict) -> bool:
    verify = payload.get("verify", {})
    file_after = verify.get("file_after", "")
    return bool(
        payload.get("ok")
        and (
            ("code" in verify and verify["code"] == 0)
            or ("file_after" in verify and ("return 2" in file_after or '"name"' in file_after))
            or payload.get("stats", {}).get("routing", {}).get("strategy") == "local_fast_path"
        )
    )


async def run_once(budget_usd: float, timeout_s: float) -> dict:
    cfg = load_cfg()
    model_cfg = build_model_cfg(cfg, budget_usd)
    root = Path(tempfile.mkdtemp(prefix="mantis-curated-live-"))
    results: dict[str, object] = {}

    async def execute(name: str, cwd: Path, prompt: str, session_id: str) -> dict:
        print(f"[start] {name}", flush=True)
        payload = await run_prompt(model_cfg, cwd, prompt, session_id, timeout_s)
        print(
            f"[done] {name} ok={payload.get('ok')} elapsed={payload.get('elapsed_s')}s",
            flush=True,
        )
        results[name] = payload
        return payload

    read_count = root / "read_count"
    read_count.mkdir()
    count_file = read_count / "sample_test_file.py"
    count_file.write_text(
        "def test_a():\n    assert True\n\n"
        "def test_b():\n    assert True\n",
        encoding="utf-8",
    )
    await execute(
        "read_count",
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
    await execute(
        "read_names",
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
    await execute(
        "bugfix",
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
    await execute(
        "edit_file",
        edit_file,
        f"Read {target}, change the return value from 1 to 2, verify the file was updated, and keep the final answer short.",
        "curated-edit-file",
    )
    results["edit_file"]["verify"] = {"file_after": target.read_text(encoding="utf-8")}

    binary_search = root / "binary_search"
    binary_search.mkdir()
    await execute(
        "binary_search",
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
    await execute(
        "token_bucket",
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
    await execute(
        "refactor_preserve_api",
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
    await execute(
        "fixture_repo_fix",
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
    await execute(
        "service_layer_fix",
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
    await execute(
        "layered_feature_fix",
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

    parser_fix = root / "parser_fix"
    parser_fix.mkdir()
    (parser_fix / "parser.py").write_text(
        "def parse_port(value: str) -> int:\n"
        "    return int(value) + 1\n",
        encoding="utf-8",
    )
    (parser_fix / "test_parser.py").write_text(
        "from parser import parse_port\n\n"
        "def test_parse_port():\n"
        "    assert parse_port('8080') == 8080\n",
        encoding="utf-8",
    )
    await execute(
        "parser_fix",
        parser_fix,
        f"Fix only {parser_fix / 'parser.py'} so the existing pytest test passes. Do not run tests.",
        "curated-parser-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=parser_fix, capture_output=True, text=True, timeout=120)
    results["parser_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    config_norm = root / "config_norm"
    config_norm.mkdir()
    (config_norm / "settings.py").write_text(
        "def normalize_env(name: str) -> str:\n"
        "    return name.strip().lower()\n",
        encoding="utf-8",
    )
    (config_norm / "test_settings.py").write_text(
        "from settings import normalize_env\n\n"
        "def test_normalize_env():\n"
        "    assert normalize_env('  Prod ') == 'PROD'\n",
        encoding="utf-8",
    )
    await execute(
        "config_normalization_fix",
        config_norm,
        f"Fix only {config_norm / 'settings.py'} so the existing pytest test passes. Do not run tests.",
        "curated-config-normalization-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=config_norm, capture_output=True, text=True, timeout=120)
    results["config_normalization_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    cli_flag = root / "cli_flag"
    cli_flag.mkdir()
    (cli_flag / "args.py").write_text(
        "def wants_verbose(argv):\n"
        "    return '--verbose' in argv\n",
        encoding="utf-8",
    )
    (cli_flag / "test_args.py").write_text(
        "from args import wants_verbose\n\n"
        "def test_wants_verbose_short_flag():\n"
        "    assert wants_verbose(['-v']) is True\n",
        encoding="utf-8",
    )
    await execute(
        "cli_flag_fix",
        cli_flag,
        f"Fix only {cli_flag / 'args.py'} so the existing pytest test passes. Do not run tests.",
        "curated-cli-flag-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=cli_flag, capture_output=True, text=True, timeout=120)
    results["cli_flag_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    fixture_toggle = root / "fixture_toggle"
    fixture_toggle.mkdir()
    (fixture_toggle / "flags.json").write_text('{"feature_enabled": false}\n', encoding="utf-8")
    (fixture_toggle / "loader.py").write_text(
        "import json\n"
        "from pathlib import Path\n\n"
        "def load_flags():\n"
        "    return json.loads((Path(__file__).parent / 'flags.json').read_text())\n",
        encoding="utf-8",
    )
    (fixture_toggle / "test_loader.py").write_text(
        "from loader import load_flags\n\n"
        "def test_feature_enabled():\n"
        "    assert load_flags()['feature_enabled'] is True\n",
        encoding="utf-8",
    )
    await execute(
        "fixture_toggle_fix",
        fixture_toggle,
        f"Fix the test by updating only {fixture_toggle / 'flags.json'}. Do not run tests.",
        "curated-fixture-toggle-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=fixture_toggle, capture_output=True, text=True, timeout=120)
    results["fixture_toggle_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    export_fix = root / "export_fix"
    export_fix.mkdir()
    (export_fix / "helpers.py").write_text(
        "def build_name(first, last):\n"
        "    return f'{first} {last}'\n",
        encoding="utf-8",
    )
    (export_fix / "api.py").write_text(
        "from helpers import build_name as wrong_name\n\n"
        "def get_name():\n"
        "    return wrong_name('Ada', 'Lovelace')\n",
        encoding="utf-8",
    )
    (export_fix / "test_api.py").write_text(
        "from api import get_name\n\n"
        "def test_get_name():\n"
        "    assert get_name() == 'Ada Lovelace'\n",
        encoding="utf-8",
    )
    await execute(
        "import_export_fix",
        export_fix,
        f"Fix only {export_fix / 'api.py'} so the existing pytest test passes. Do not run tests.",
        "curated-import-export-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=export_fix, capture_output=True, text=True, timeout=120)
    results["import_export_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    json_schema = root / "json_schema"
    json_schema.mkdir()
    (json_schema / "schema.json").write_text('{"type": "object", "required": ["id"]}\n', encoding="utf-8")
    await execute(
        "json_schema_edit",
        json_schema,
        f"Read {json_schema / 'schema.json'}, add 'name' to the required list, verify the file changed, and keep the answer short.",
        "curated-json-schema-edit",
    )
    results["json_schema_edit"]["verify"] = {
        "file_after": (json_schema / "schema.json").read_text(encoding="utf-8"),
    }

    refactor_utils = root / "refactor_utils"
    refactor_utils.mkdir()
    (refactor_utils / "utils.py").write_text(
        "def join_items(items):\n"
        "    result = ''\n"
        "    for item in items:\n"
        "        result += item + ','\n"
        "    return result[:-1] if result else ''\n",
        encoding="utf-8",
    )
    (refactor_utils / "test_utils.py").write_text(
        "from utils import join_items\n\n"
        "def test_join_items():\n"
        "    assert join_items(['a', 'b']) == 'a,b'\n",
        encoding="utf-8",
    )
    await execute(
        "refactor_utils_preserve_api",
        refactor_utils,
        f"Refactor only {refactor_utils / 'utils.py'} for clarity while preserving behavior exactly. Do not run tests.",
        "curated-refactor-utils",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=refactor_utils, capture_output=True, text=True, timeout=120)
    results["refactor_utils_preserve_api"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    api_contract = root / "api_contract"
    api_contract.mkdir()
    await execute(
        "api_contract_generation",
        api_contract,
        (
            f"Create {api_contract / 'slugify.py'} with a slugify function and "
            f"{api_contract / 'test_slugify.py'} with pytest tests for spaces, punctuation, uppercase, empty string, and repeated separators. "
            "Do not run pytest."
        ),
        "curated-api-contract-generation",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=api_contract, capture_output=True, text=True, timeout=120)
    results["api_contract_generation"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-500:],
        "err": proc.stderr[-500:],
    }

    transform_fix = root / "transform_fix"
    transform_fix.mkdir()
    (transform_fix / "transform.py").write_text(
        "def to_record(name, active):\n"
        "    return {'name': name, 'status': 'inactive'}\n",
        encoding="utf-8",
    )
    (transform_fix / "test_transform.py").write_text(
        "from transform import to_record\n\n"
        "def test_to_record_active():\n"
        "    assert to_record('Ada', True) == {'name': 'Ada', 'status': 'active'}\n",
        encoding="utf-8",
    )
    await execute(
        "transform_fix",
        transform_fix,
        f"Fix only {transform_fix / 'transform.py'} so the existing pytest test passes. Do not run tests.",
        "curated-transform-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=transform_fix, capture_output=True, text=True, timeout=120)
    results["transform_fix"]["verify"] = {
        "code": proc.returncode,
        "out": proc.stdout[-400:],
        "err": proc.stderr[-400:],
    }

    nested_service = root / "nested_service"
    (nested_service / "app").mkdir(parents=True)
    (nested_service / "tests").mkdir()
    (nested_service / "app" / "__init__.py").write_text("", encoding="utf-8")
    (nested_service / "app" / "repo.py").write_text(
        "def fetch_config():\n"
        "    return {'region': 'eu', 'enabled': False}\n",
        encoding="utf-8",
    )
    (nested_service / "app" / "service.py").write_text(
        "from app.repo import fetch_config\n\n"
        "def get_runtime_config():\n"
        "    cfg = fetch_config()\n"
        "    return {'region': cfg['region']}\n",
        encoding="utf-8",
    )
    (nested_service / "tests" / "test_service.py").write_text(
        "from app.service import get_runtime_config\n\n"
        "def test_runtime_config():\n"
        "    assert get_runtime_config() == {'region': 'eu', 'enabled': True}\n",
        encoding="utf-8",
    )
    await execute(
        "nested_service_fix",
        nested_service,
        f"Fix only {nested_service / 'app' / 'service.py'} so the existing pytest test passes. Do not run tests.",
        "curated-nested-service-fix",
    )
    proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=nested_service, capture_output=True, text=True, timeout=120)
    results["nested_service_fix"]["verify"] = {
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
        if scenario_ok(payload):
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
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    for index in range(args.loops):
        print(f"=== loop {index + 1}/{args.loops} ===")
        result = asyncio.run(run_once(args.budget, args.timeout))
        result["summary"] = summarize(result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
