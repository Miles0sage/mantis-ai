#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx

from mantis.app import MantisApp
from mantis.core.hooks import Decision
from mantis.core.planner import build_execution_plan
from mantis.core.quality_gate import verify_cascade
from mantis.server import app as server_app


def _record(name: str, started: float, passed: bool, details: dict | None = None) -> dict:
    return {
        "name": name,
        "passed": passed,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "details": details or {},
    }


async def _poll_job(client: httpx.AsyncClient, job_id: str, target_statuses: set[str], attempts: int = 40):
    final_job = None
    for _ in range(attempts):
        response = await client.get(f"/api/jobs/{job_id}")
        final_job = response.json()
        if final_job["status"] in target_statuses:
            return final_job
        await asyncio.sleep(0.05)
    return final_job


async def scenario_python_multifile_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-pass"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "token_bucket.py").write_text(
        "class TokenBucket:\n"
        "    def __init__(self):\n"
        "        self.tokens = 1\n",
        encoding="utf-8",
    )
    (task_dir / "helpers.py").write_text(
        "def allow(tokens: int = 1):\n"
        "    return True\n\n"
        "def available():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    prompt = (
        "Create token_bucket.py implementing a TokenBucket class with methods "
        "__init__, allow(tokens: int = 1), available()."
    )
    plan = build_execution_plan(prompt)
    score, reason = await verify_cascade(
        "feature",
        "written and saved",
        [str(task_dir / "token_bucket.py"), str(task_dir / "helpers.py")],
        prompt=prompt,
    )
    passed = (
        len(plan.tasks) == 1
        and "class exists: TokenBucket" in plan.tasks[0].postconditions
        and score >= 0.8
        and "Tier 2.6 FAIL" not in reason
    )
    return _record(
        "python_multifile_contract_pass",
        started,
        passed,
        {
            "score": score,
            "reason": reason,
            "postconditions": plan.tasks[0].postconditions,
        },
    )


async def scenario_python_interface_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-fail"
    task_dir.mkdir(parents=True, exist_ok=True)
    target = task_dir / "token_bucket.py"
    target.write_text(
        "class WrongName:\n"
        "    def available(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    prompt = (
        "Create token_bucket.py implementing a TokenBucket class with methods "
        "__init__, allow(tokens: int = 1), available()."
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved",
        [str(target)],
        prompt=prompt,
    )
    passed = score == 0.46 and "Tier 2.6 FAIL" in reason
    return _record(
        "python_interface_fail_fast",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_javascript_multifile_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "js-pass"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "auth.js").write_text(
        "class AuthService {}\n"
        "module.exports = { AuthService };\n",
        encoding="utf-8",
    )
    (task_dir / "session.js").write_text(
        "function createSession(userId) { return { userId }; }\n"
        "module.exports = { createSession };\n",
        encoding="utf-8",
    )
    prompt = "Create auth.js implementing an AuthService class and createSession function."
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [str(task_dir / "auth.js"), str(task_dir / "session.js")],
        prompt=prompt,
    )
    passed = score >= 0.8 and "Tier 2.7 FAIL" not in reason
    return _record(
        "javascript_multifile_contract_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_guardrail_blocks_raw_edits(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "guardrail"
    task_dir.mkdir(parents=True, exist_ok=True)
    target = task_dir / "edit_me.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")
    app = MantisApp({"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"}, project_dir=str(task_dir))
    result = await app.hook_manager.run_pre_tool(
        "edit_file",
        {"file_path": str(target), "old_text": "return 1", "new_text": "return 2"},
    )
    passed = result.decision == Decision.BLOCK and "semantic tools first" in result.reason.lower()
    return _record(
        "python_guardrail_blocks_raw_edit",
        started,
        passed,
        {"decision": result.decision.value, "reason": result.reason},
    )


async def scenario_python_repo_bugfix_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-repo-pass"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "calc.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    (task_dir / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "bug_fix",
        "written and saved successfully",
        [str(task_dir / "calc.py")],
        cwd=str(task_dir),
        prompt=f"Fix only {task_dir / 'calc.py'} so the existing pytest test will pass.",
    )
    passed = score >= 0.8 and "Tier 2 FAIL" not in reason
    return _record(
        "python_repo_bugfix_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_repo_bugfix_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-repo-fail"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "calc.py").write_text(
        "def add(a, b):\n"
        "    return a - b\n",
        encoding="utf-8",
    )
    (task_dir / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "bug_fix",
        "written and saved successfully",
        [str(task_dir / "calc.py")],
        cwd=str(task_dir),
        prompt=f"Fix only {task_dir / 'calc.py'} so the existing pytest test will pass.",
    )
    passed = score == 0.4 and "Tier 2 FAIL" in reason
    return _record(
        "python_repo_bugfix_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_repo_test_writing_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-test-writing-pass"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "math_utils.py").write_text(
        "def multiply(a, b):\n"
        "    return a * b\n",
        encoding="utf-8",
    )
    (task_dir / "test_math_utils.py").write_text(
        "from math_utils import multiply\n\n"
        "def test_multiply_positive():\n"
        "    assert multiply(3, 4) == 12\n\n"
        "def test_multiply_zero():\n"
        "    assert multiply(10, 0) == 0\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "test_writing",
        "written and saved successfully",
        [str(task_dir / "math_utils.py"), str(task_dir / "test_math_utils.py")],
        cwd=str(task_dir),
        prompt=f"Create {task_dir / 'test_math_utils.py'} with pytest tests for {task_dir / 'math_utils.py'}.",
    )
    passed = score >= 0.8 and "Tier 2 FAIL" not in reason
    return _record(
        "python_repo_test_writing_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_repo_test_writing_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-test-writing-fail"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "math_utils.py").write_text(
        "def multiply(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    (task_dir / "test_math_utils.py").write_text(
        "from math_utils import multiply\n\n"
        "def test_multiply_positive():\n"
        "    assert multiply(3, 4) == 12\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "test_writing",
        "written and saved successfully",
        [str(task_dir / "math_utils.py"), str(task_dir / "test_math_utils.py")],
        cwd=str(task_dir),
        prompt=f"Create {task_dir / 'test_math_utils.py'} with pytest tests for {task_dir / 'math_utils.py'}.",
    )
    passed = score == 0.4 and "Tier 2 FAIL" in reason
    return _record(
        "python_repo_test_writing_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_multifile_feature_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-feature-pass"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "store.py").write_text(
        "class Store:\n"
        "    def __init__(self):\n"
        "        self._items = {'a': 1}\n\n"
        "    def get(self, key):\n"
        "        return self._items.get(key)\n",
        encoding="utf-8",
    )
    (task_dir / "api.py").write_text(
        "from store import Store\n\n"
        "def get_item(key):\n"
        "    return Store().get(key)\n",
        encoding="utf-8",
    )
    (task_dir / "test_api.py").write_text(
        "from api import get_item\n\n"
        "def test_get_item_existing():\n"
        "    assert get_item('a') == 1\n\n"
        "def test_get_item_missing():\n"
        "    assert get_item('missing') is None\n",
        encoding="utf-8",
    )
    prompt = (
        f"Create {task_dir / 'store.py'}, {task_dir / 'api.py'}, and {task_dir / 'test_api.py'} "
        "implementing a Store class and get_item function with pytest coverage."
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [str(task_dir / "store.py"), str(task_dir / "api.py"), str(task_dir / "test_api.py")],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score >= 0.8 and "Tier 2 FAIL" not in reason and "Tier 2.6 FAIL" not in reason
    return _record(
        "python_multifile_feature_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_multifile_feature_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-feature-fail"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "store.py").write_text(
        "class WrongStore:\n"
        "    def __init__(self):\n"
        "        self._items = {'a': 1}\n",
        encoding="utf-8",
    )
    (task_dir / "api.py").write_text(
        "from store import WrongStore\n\n"
        "def wrong_lookup(key):\n"
        "    return WrongStore()._items.get(key)\n",
        encoding="utf-8",
    )
    (task_dir / "test_api.py").write_text(
        "from api import wrong_lookup\n\n"
        "def test_get_item_existing():\n"
        "    assert wrong_lookup('a') == 2\n",
        encoding="utf-8",
    )
    prompt = (
        f"Create {task_dir / 'store.py'}, {task_dir / 'api.py'}, and {task_dir / 'test_api.py'} "
        "implementing a Store class and get_item function with pytest coverage."
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [str(task_dir / "store.py"), str(task_dir / "api.py"), str(task_dir / "test_api.py")],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score < 0.8 and ("Tier 2 FAIL" in reason or "Tier 2.6 FAIL" in reason)
    return _record(
        "python_multifile_feature_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_refactor_preserve_api_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-refactor-pass"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "payments.py").write_text(
        "def calculate_total(subtotal, tax_rate):\n"
        "    return subtotal + (subtotal * tax_rate)\n\n"
        "def format_total(amount):\n"
        "    return f'$ {amount:.2f}'\n",
        encoding="utf-8",
    )
    (task_dir / "test_payments.py").write_text(
        "from payments import calculate_total, format_total\n\n"
        "def test_calculate_total():\n"
        "    assert calculate_total(100, 0.1) == 110\n\n"
        "def test_format_total():\n"
        "    assert format_total(110) == '$ 110.00'\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "refactor",
        "written and saved successfully",
        [str(task_dir / "payments.py"), str(task_dir / "test_payments.py")],
        cwd=str(task_dir),
        prompt=(
            f"Refactor only {task_dir / 'payments.py'} to improve readability while preserving "
            "the public API of calculate_total and format_total so the existing pytest tests still pass."
        ),
    )
    passed = score >= 0.8 and "Tier 2 FAIL" not in reason
    return _record(
        "python_refactor_preserve_api_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_refactor_preserve_api_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-refactor-fail"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "payments.py").write_text(
        "def calculate(subtotal, tax_rate):\n"
        "    return subtotal + (subtotal * tax_rate)\n\n"
        "def render_total(amount):\n"
        "    return f'$ {amount:.2f}'\n",
        encoding="utf-8",
    )
    (task_dir / "test_payments.py").write_text(
        "from payments import calculate_total, format_total\n\n"
        "def test_calculate_total():\n"
        "    assert calculate_total(100, 0.1) == 110\n\n"
        "def test_format_total():\n"
        "    assert format_total(110) == '$ 110.00'\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "refactor",
        "written and saved successfully",
        [str(task_dir / "payments.py"), str(task_dir / "test_payments.py")],
        cwd=str(task_dir),
        prompt=(
            f"Refactor only {task_dir / 'payments.py'} to improve readability while preserving "
            "the public API of calculate_total and format_total so the existing pytest tests still pass."
        ),
    )
    passed = score < 0.8 and "Tier 2 FAIL" in reason
    return _record(
        "python_refactor_preserve_api_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_wrong_file_change_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "wrong-file-change-fail"
    task_dir.mkdir(parents=True, exist_ok=True)
    target = task_dir / "target.py"
    distractor = task_dir / "other.py"
    target.write_text(
        "def needed_value():\n"
        "    return 3\n",
        encoding="utf-8",
    )
    distractor.write_text(
        "def needed_value():\n"
        "    return 7\n",
        encoding="utf-8",
    )
    (task_dir / "test_target.py").write_text(
        "from target import needed_value\n\n"
        "def test_needed_value():\n"
        "    assert needed_value() == 5\n",
        encoding="utf-8",
    )
    prompt = f"Update only {target} so it defines needed_value() returning 5 and existing pytest tests pass."
    score, reason = await verify_cascade(
        "bug_fix",
        "written and saved successfully",
        [str(target), str(task_dir / "test_target.py"), str(distractor)],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score < 0.8 and "Tier 2 FAIL" in reason
    return _record(
        "wrong_file_change_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_fixture_repo_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-fixture-pass"
    fixture_dir = task_dir / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "loader.py").write_text(
        "import json\n"
        "from pathlib import Path\n\n"
        "def load_user(path):\n"
        "    return json.loads(Path(path).read_text())\n",
        encoding="utf-8",
    )
    (fixture_dir / "user.json").write_text(
        '{"name": "Ada", "active": true}\n',
        encoding="utf-8",
    )
    (task_dir / "test_loader.py").write_text(
        "from pathlib import Path\n"
        "from loader import load_user\n\n"
        "def test_load_user_fixture():\n"
        "    user = load_user(Path('fixtures/user.json'))\n"
        "    assert user['name'] == 'Ada'\n"
        "    assert user['active'] is True\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [str(task_dir / "loader.py"), str(task_dir / "test_loader.py")],
        cwd=str(task_dir),
        prompt=(
            f"Create {task_dir / 'loader.py'} and {task_dir / 'test_loader.py'} so fixture-backed "
            "pytest tests for fixtures/user.json pass."
        ),
    )
    passed = score >= 0.8 and "Tier 2 FAIL" not in reason
    return _record(
        "python_fixture_repo_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_fixture_repo_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-fixture-fail"
    fixture_dir = task_dir / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "loader.py").write_text(
        "def load_user(path):\n"
        "    return {'name': 'Wrong', 'active': False}\n",
        encoding="utf-8",
    )
    (fixture_dir / "user.json").write_text(
        '{"name": "Ada", "active": true}\n',
        encoding="utf-8",
    )
    (task_dir / "test_loader.py").write_text(
        "from pathlib import Path\n"
        "from loader import load_user\n\n"
        "def test_load_user_fixture():\n"
        "    user = load_user(Path('fixtures/user.json'))\n"
        "    assert user['name'] == 'Ada'\n"
        "    assert user['active'] is True\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [str(task_dir / "loader.py"), str(task_dir / "test_loader.py")],
        cwd=str(task_dir),
        prompt=(
            f"Create {task_dir / 'loader.py'} and {task_dir / 'test_loader.py'} so fixture-backed "
            "pytest tests for fixtures/user.json pass."
        ),
    )
    passed = score < 0.8 and "Tier 2 FAIL" in reason
    return _record(
        "python_fixture_repo_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_service_layer_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-service-layer-pass"
    package_dir = task_dir / "app"
    tests_dir = task_dir / "tests"
    package_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "models.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class User:\n"
        "    id: int\n"
        "    name: str\n"
        "    active: bool = True\n",
        encoding="utf-8",
    )
    (package_dir / "repository.py").write_text(
        "from app.models import User\n\n"
        "def get_user(user_id: int) -> User:\n"
        "    if user_id == 1:\n"
        "        return User(id=1, name='Ada', active=True)\n"
        "    raise KeyError(user_id)\n",
        encoding="utf-8",
    )
    (package_dir / "service.py").write_text(
        "from app.repository import get_user\n\n"
        "def load_user_profile(user_id: int) -> dict:\n"
        "    user = get_user(user_id)\n"
        "    return {'id': user.id, 'name': user.name, 'active': user.active}\n",
        encoding="utf-8",
    )
    (package_dir / "api.py").write_text(
        "from app.service import load_user_profile\n\n"
        "def get_user_profile(user_id: int) -> dict:\n"
        "    return load_user_profile(user_id)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_api.py").write_text(
        "import pytest\n"
        "from app.api import get_user_profile\n\n"
        "def test_get_user_profile_existing():\n"
        "    profile = get_user_profile(1)\n"
        "    assert profile == {'id': 1, 'name': 'Ada', 'active': True}\n\n"
        "def test_get_user_profile_missing():\n"
        "    with pytest.raises(KeyError):\n"
        "        get_user_profile(9)\n",
        encoding="utf-8",
    )
    prompt = (
        f"Create {package_dir / 'models.py'}, {package_dir / 'repository.py'}, "
        f"{package_dir / 'service.py'}, {package_dir / 'api.py'}, and {tests_dir / 'test_api.py'} "
        "implementing User, get_user, load_user_profile, and get_user_profile with pytest coverage."
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [
            str(package_dir / "models.py"),
            str(package_dir / "repository.py"),
            str(package_dir / "service.py"),
            str(package_dir / "api.py"),
            str(tests_dir / "test_api.py"),
        ],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score >= 0.8 and "Tier 2 FAIL" not in reason and "Tier 2.6 FAIL" not in reason
    return _record(
        "python_service_layer_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_service_layer_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-service-layer-fail"
    package_dir = task_dir / "app"
    tests_dir = task_dir / "tests"
    package_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "models.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class WrongUser:\n"
        "    id: int\n"
        "    label: str\n",
        encoding="utf-8",
    )
    (package_dir / "repository.py").write_text(
        "def fetch_user(user_id: int) -> dict:\n"
        "    return {'id': user_id, 'name': 'Wrong'}\n",
        encoding="utf-8",
    )
    (package_dir / "service.py").write_text(
        "from app.repository import fetch_user\n\n"
        "def render_user(user_id: int) -> dict:\n"
        "    return fetch_user(user_id)\n",
        encoding="utf-8",
    )
    (package_dir / "api.py").write_text(
        "from app.service import render_user\n\n"
        "def fetch_profile(user_id: int) -> dict:\n"
        "    return render_user(user_id)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_api.py").write_text(
        "from app.api import fetch_profile\n\n"
        "def test_get_user_profile_existing():\n"
        "    profile = fetch_profile(1)\n"
        "    assert profile == {'id': 1, 'name': 'Ada', 'active': True}\n",
        encoding="utf-8",
    )
    prompt = (
        f"Create {package_dir / 'models.py'}, {package_dir / 'repository.py'}, "
        f"{package_dir / 'service.py'}, {package_dir / 'api.py'}, and {tests_dir / 'test_api.py'} "
        "implementing User, get_user, load_user_profile, and get_user_profile with pytest coverage."
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [
            str(package_dir / "models.py"),
            str(package_dir / "repository.py"),
            str(package_dir / "service.py"),
            str(package_dir / "api.py"),
            str(tests_dir / "test_api.py"),
        ],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score < 0.8 and ("Tier 2 FAIL" in reason or "Tier 2.6 FAIL" in reason)
    return _record(
        "python_service_layer_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_layered_feature_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-layered-feature-pass"
    package_dir = task_dir / "app"
    tests_dir = task_dir / "tests"
    package_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "models.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class User:\n"
        "    id: int\n"
        "    name: str\n"
        "    active: bool\n\n"
        "@dataclass\n"
        "class UserDTO:\n"
        "    id: int\n"
        "    display_name: str\n"
        "    status: str\n",
        encoding="utf-8",
    )
    (package_dir / "repository.py").write_text(
        "from app.models import User\n\n"
        "def get_user(user_id: int) -> User:\n"
        "    if user_id == 1:\n"
        "        return User(id=1, name='Ada Lovelace', active=True)\n"
        "    if user_id == 2:\n"
        "        return User(id=2, name='Grace Hopper', active=False)\n"
        "    raise KeyError(user_id)\n",
        encoding="utf-8",
    )
    (package_dir / "mappers.py").write_text(
        "from app.models import User, UserDTO\n\n"
        "def to_user_dto(user: User) -> UserDTO:\n"
        "    status = 'active' if user.active else 'inactive'\n"
        "    return UserDTO(id=user.id, display_name=user.name.upper(), status=status)\n",
        encoding="utf-8",
    )
    (package_dir / "service.py").write_text(
        "from app.mappers import to_user_dto\n"
        "from app.repository import get_user\n\n"
        "def load_user_dto(user_id: int):\n"
        "    return to_user_dto(get_user(user_id))\n",
        encoding="utf-8",
    )
    (package_dir / "api.py").write_text(
        "from app.service import load_user_dto\n\n"
        "def get_user_summary(user_id: int) -> dict:\n"
        "    dto = load_user_dto(user_id)\n"
        "    return {'id': dto.id, 'display_name': dto.display_name, 'status': dto.status}\n",
        encoding="utf-8",
    )
    (tests_dir / "test_api.py").write_text(
        "import pytest\n"
        "from app.api import get_user_summary\n\n"
        "def test_get_user_summary_active():\n"
        "    assert get_user_summary(1) == {'id': 1, 'display_name': 'ADA LOVELACE', 'status': 'active'}\n\n"
        "def test_get_user_summary_inactive():\n"
        "    assert get_user_summary(2) == {'id': 2, 'display_name': 'GRACE HOPPER', 'status': 'inactive'}\n\n"
        "def test_get_user_summary_missing():\n"
        "    with pytest.raises(KeyError):\n"
        "        get_user_summary(99)\n",
        encoding="utf-8",
    )
    prompt = (
        f"Create {package_dir / 'models.py'}, {package_dir / 'repository.py'}, "
        f"{package_dir / 'mappers.py'}, {package_dir / 'service.py'}, {package_dir / 'api.py'}, "
        f"and {tests_dir / 'test_api.py'} implementing User, UserDTO, get_user, to_user_dto, "
        "load_user_dto, and get_user_summary with pytest coverage."
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [
            str(package_dir / "models.py"),
            str(package_dir / "repository.py"),
            str(package_dir / "mappers.py"),
            str(package_dir / "service.py"),
            str(package_dir / "api.py"),
            str(tests_dir / "test_api.py"),
        ],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score >= 0.8 and "Tier 2 FAIL" not in reason and "Tier 2.6 FAIL" not in reason
    return _record(
        "python_layered_feature_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_layered_feature_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-layered-feature-fail"
    package_dir = task_dir / "app"
    tests_dir = task_dir / "tests"
    package_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "models.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class WrongUser:\n"
        "    id: int\n"
        "    label: str\n",
        encoding="utf-8",
    )
    (package_dir / "repository.py").write_text(
        "def fetch_user(user_id: int) -> dict:\n"
        "    return {'id': user_id, 'name': 'wrong', 'active': False}\n",
        encoding="utf-8",
    )
    (package_dir / "mappers.py").write_text(
        "def map_user(user: dict) -> dict:\n"
        "    return {'id': user['id'], 'display_name': user['name'], 'status': 'unknown'}\n",
        encoding="utf-8",
    )
    (package_dir / "service.py").write_text(
        "from app.mappers import map_user\n"
        "from app.repository import fetch_user\n\n"
        "def render_user(user_id: int):\n"
        "    return map_user(fetch_user(user_id))\n",
        encoding="utf-8",
    )
    (package_dir / "api.py").write_text(
        "from app.service import render_user\n\n"
        "def fetch_user_summary(user_id: int) -> dict:\n"
        "    return render_user(user_id)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_api.py").write_text(
        "import pytest\n"
        "from app.api import fetch_user_summary\n\n"
        "def test_get_user_summary_active():\n"
        "    assert fetch_user_summary(1) == {'id': 1, 'display_name': 'ADA LOVELACE', 'status': 'active'}\n\n"
        "def test_get_user_summary_missing():\n"
        "    with pytest.raises(KeyError):\n"
        "        fetch_user_summary(99)\n",
        encoding="utf-8",
    )
    prompt = (
        f"Create {package_dir / 'models.py'}, {package_dir / 'repository.py'}, "
        f"{package_dir / 'mappers.py'}, {package_dir / 'service.py'}, {package_dir / 'api.py'}, "
        f"and {tests_dir / 'test_api.py'} implementing User, UserDTO, get_user, to_user_dto, "
        "load_user_dto, and get_user_summary with pytest coverage."
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [
            str(package_dir / "models.py"),
            str(package_dir / "repository.py"),
            str(package_dir / "mappers.py"),
            str(package_dir / "service.py"),
            str(package_dir / "api.py"),
            str(tests_dir / "test_api.py"),
        ],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score < 0.8 and ("Tier 2 FAIL" in reason or "Tier 2.6 FAIL" in reason)
    return _record(
        "python_layered_feature_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_configured_service_pass(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-configured-service-pass"
    package_dir = task_dir / "app"
    tests_dir = task_dir / "tests"
    package_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "config.py").write_text(
        "SETTINGS = {\n"
        "    'currency': 'EUR',\n"
        "    'decimal_places': 2,\n"
        "}\n",
        encoding="utf-8",
    )
    (package_dir / "repository.py").write_text(
        "def get_order(order_id: int) -> dict:\n"
        "    if order_id == 1:\n"
        "        return {'id': 1, 'subtotal': 12.5, 'tax_rate': 0.2}\n"
        "    raise KeyError(order_id)\n",
        encoding="utf-8",
    )
    (package_dir / "pricing.py").write_text(
        "def compute_total(subtotal: float, tax_rate: float) -> float:\n"
        "    return subtotal + (subtotal * tax_rate)\n",
        encoding="utf-8",
    )
    (package_dir / "service.py").write_text(
        "from app.config import SETTINGS\n"
        "from app.pricing import compute_total\n"
        "from app.repository import get_order\n\n"
        "def load_order_summary(order_id: int) -> dict:\n"
        "    order = get_order(order_id)\n"
        "    total = compute_total(order['subtotal'], order['tax_rate'])\n"
        "    return {\n"
        "        'id': order['id'],\n"
        "        'currency': SETTINGS['currency'],\n"
        "        'total': round(total, SETTINGS['decimal_places']),\n"
        "    }\n",
        encoding="utf-8",
    )
    (package_dir / "api.py").write_text(
        "from app.service import load_order_summary\n\n"
        "def get_order_summary(order_id: int) -> dict:\n"
        "    return load_order_summary(order_id)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_api.py").write_text(
        "import pytest\n"
        "from app.api import get_order_summary\n\n"
        "def test_get_order_summary():\n"
        "    assert get_order_summary(1) == {'id': 1, 'currency': 'EUR', 'total': 15.0}\n\n"
        "def test_get_order_summary_missing():\n"
        "    with pytest.raises(KeyError):\n"
        "        get_order_summary(9)\n",
        encoding="utf-8",
    )
    prompt = (
        f"Create {package_dir / 'config.py'}, {package_dir / 'repository.py'}, {package_dir / 'pricing.py'}, "
        f"{package_dir / 'service.py'}, {package_dir / 'api.py'}, and {tests_dir / 'test_api.py'} implementing "
        "SETTINGS, get_order, compute_total, load_order_summary, and get_order_summary with pytest coverage."
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved successfully",
        [
            str(package_dir / "config.py"),
            str(package_dir / "repository.py"),
            str(package_dir / "pricing.py"),
            str(package_dir / "service.py"),
            str(package_dir / "api.py"),
            str(tests_dir / "test_api.py"),
        ],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score >= 0.8 and "Tier 2 FAIL" not in reason and "Tier 2.6 FAIL" not in reason
    return _record(
        "python_configured_service_pass",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_python_cross_module_refactor_fail(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "python-cross-module-refactor-fail"
    package_dir = task_dir / "app"
    tests_dir = task_dir / "tests"
    package_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "utils.py").write_text(
        "def normalize_name(name: str) -> str:\n"
        "    return name.strip().title()\n",
        encoding="utf-8",
    )
    (package_dir / "repository.py").write_text(
        "def get_user(user_id: int) -> dict:\n"
        "    if user_id == 1:\n"
        "        return {'id': 1, 'name': ' ada lovelace '}\n"
        "    raise KeyError(user_id)\n",
        encoding="utf-8",
    )
    (package_dir / "service.py").write_text(
        "from app.repository import get_user\n"
        "from app.utils import wrong_name\n\n"
        "def load_user_name(user_id: int) -> dict:\n"
        "    user = get_user(user_id)\n"
        "    return {'id': user['id'], 'name': wrong_name(user['name'])}\n",
        encoding="utf-8",
    )
    (package_dir / "api.py").write_text(
        "from app.service import load_user_name\n\n"
        "def get_user_name(user_id: int) -> dict:\n"
        "    return load_user_name(user_id)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_api.py").write_text(
        "from app.api import get_user_name\n\n"
        "def test_get_user_name():\n"
        "    assert get_user_name(1) == {'id': 1, 'name': 'Ada Lovelace'}\n",
        encoding="utf-8",
    )
    prompt = (
        f"Refactor {package_dir / 'utils.py'}, {package_dir / 'service.py'}, and {package_dir / 'api.py'} "
        "while preserving normalize_name and get_user_name so the existing pytest tests still pass."
    )
    score, reason = await verify_cascade(
        "refactor",
        "written and saved successfully",
        [
            str(package_dir / "utils.py"),
            str(package_dir / "repository.py"),
            str(package_dir / "service.py"),
            str(package_dir / "api.py"),
            str(tests_dir / "test_api.py"),
        ],
        cwd=str(task_dir),
        prompt=prompt,
    )
    passed = score < 0.8 and "Tier 2 FAIL" in reason
    return _record(
        "python_cross_module_refactor_fail",
        started,
        passed,
        {"score": score, "reason": reason},
    )


async def scenario_background_job_complete(root: Path) -> dict:
    started = time.perf_counter()
    task_dir = root / "background-job"
    task_dir.mkdir(parents=True, exist_ok=True)
    target = task_dir / "edit_me.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")

    async def fake_run_chat(self, prompt: str, job_id: str | None = None) -> str:
        target.write_text("def value():\n    return 2\n", encoding="utf-8")
        return "updated"

    with patch.dict("os.environ", {"MANTIS_API_KEY": "test-key"}, clear=False), patch(
        "mantis.app.MantisApp._run_chat", fake_run_chat
    ):
        transport = httpx.ASGITransport(app=server_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/jobs",
                json={"prompt": f"edit {target}", "session_id": "scenario-background"},
            )
            if response.status_code != 200:
                return _record(
                    "background_job_complete",
                    started,
                    False,
                    {"status_code": response.status_code, "body": response.text},
                )
            job_id = response.json()["job_id"]
            final_job = await _poll_job(client, job_id, {"done", "failed"})
            passed = (
                final_job is not None
                and final_job["status"] == "done"
                and target.read_text(encoding="utf-8") == "def value():\n    return 2\n"
            )
            return _record(
                "background_job_complete",
                started,
                passed,
                {"job_status": final_job["status"] if final_job else "missing"},
            )


async def scenario_model_approval_resume(root: Path) -> dict:
    started = time.perf_counter()

    async def fake_run_agentic(self, prompt: str, system_prompt: str | None = None) -> str:
        return "escalated result"

    def fake_route(self, prompt: str):
        profile = next(model for model in self.router.list_models() if model.name == "claude-3-5-sonnet")
        return profile, {
            "strategy": "auto_plan_router",
            "task_type": "feature",
            "complexity": "high",
            "file_count": 3,
            "task_count": 2,
            "needs_escalation": True,
        }

    with patch.dict(
        "os.environ",
        {"OPENAI_API_KEY": "test-openai", "ANTHROPIC_API_KEY": "test-anthropic", "HOME": str(root)},
        clear=False,
    ), patch(
        "mantis.server._resolve_config",
        lambda overrides=None: {
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "openai_api_key": "test-openai",
            "anthropic_api_key": "test-anthropic",
            "budget_usd": None,
            "explicit_model": False,
        },
    ), patch(
        "mantis.app.MantisApp._resolve_model_for_prompt", fake_route
    ), patch(
        "mantis.app.MantisApp._should_use_orchestrator", lambda self, routing: False
    ), patch(
        "mantis.core.query_engine.QueryEngine.run_agentic", fake_run_agentic
    ):
        transport = httpx.ASGITransport(app=server_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/jobs",
                json={"prompt": "refactor a.py and b.py and c.py", "session_id": "scenario-approval"},
            )
            if response.status_code != 200:
                return _record(
                    "model_approval_resume",
                    started,
                    False,
                    {"status_code": response.status_code, "body": response.text},
                )
            job_id = response.json()["job_id"]
            waiting_job = await _poll_job(client, job_id, {"awaiting_approval", "done", "failed"})
            if not waiting_job or waiting_job["status"] != "awaiting_approval":
                return _record(
                    "model_approval_resume",
                    started,
                    False,
                    {"job_status": waiting_job["status"] if waiting_job else "missing"},
                )
            approval_id = waiting_job["metadata"]["approval_id"]
            approve = await client.post(
                f"/api/approvals/{approval_id}/approve",
                json={"note": "allow stronger model"},
            )
            if approve.status_code != 200:
                return _record(
                    "model_approval_resume",
                    started,
                    False,
                    {"approve_status": approve.status_code, "approve_body": approve.text},
                )
            final_job = await _poll_job(client, job_id, {"done", "failed"})
            passed = (
                final_job is not None
                and final_job["status"] == "done"
                and final_job["response"] == "escalated result"
            )
            return _record(
                "model_approval_resume",
                started,
                passed,
                {"job_status": final_job["status"] if final_job else "missing"},
            )


async def run_once(include_server: bool = False) -> dict:
    root = Path(tempfile.mkdtemp(prefix="mantis-scenario-benchmark-"))
    results = []
    results.append(await scenario_python_multifile_pass(root))
    results.append(await scenario_python_interface_fail(root))
    results.append(await scenario_javascript_multifile_pass(root))
    results.append(await scenario_python_guardrail_blocks_raw_edits(root))
    results.append(await scenario_python_repo_bugfix_pass(root))
    results.append(await scenario_python_repo_bugfix_fail(root))
    results.append(await scenario_python_repo_test_writing_pass(root))
    results.append(await scenario_python_repo_test_writing_fail(root))
    results.append(await scenario_python_multifile_feature_pass(root))
    results.append(await scenario_python_multifile_feature_fail(root))
    results.append(await scenario_python_refactor_preserve_api_pass(root))
    results.append(await scenario_python_refactor_preserve_api_fail(root))
    results.append(await scenario_wrong_file_change_fail(root))
    results.append(await scenario_python_fixture_repo_pass(root))
    results.append(await scenario_python_fixture_repo_fail(root))
    results.append(await scenario_python_service_layer_pass(root))
    results.append(await scenario_python_service_layer_fail(root))
    results.append(await scenario_python_layered_feature_pass(root))
    results.append(await scenario_python_layered_feature_fail(root))
    results.append(await scenario_python_configured_service_pass(root))
    results.append(await scenario_python_cross_module_refactor_fail(root))
    if include_server:
        results.append(await scenario_background_job_complete(root))
        results.append(await scenario_model_approval_resume(root))
    passed = sum(1 for result in results if result["passed"])
    return {
        "root": str(root),
        "include_server": include_server,
        "pass_count": passed,
        "total": len(results),
        "pass_rate": round(passed / len(results), 3),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local MANTIS scenario benchmark.")
    parser.add_argument("--loops", type=int, default=1)
    parser.add_argument("--include-server", action="store_true")
    parser.add_argument("--report-dir", type=str, default=".omc/state/benchmark-reports")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for index in range(args.loops):
        result = asyncio.run(run_once(include_server=args.include_server))
        print(f"=== loop {index + 1}/{args.loops} ===")
        print(json.dumps(result, indent=2))
        summaries.append(result["pass_rate"])
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        loop_path = report_dir / f"scenario-benchmark-{timestamp}-loop{index + 1}.json"
        loop_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    avg = sum(summaries) / len(summaries)
    summary = {"loops": args.loops, "average_pass_rate": round(avg, 3)}
    print(json.dumps(summary, indent=2))
    (report_dir / "latest-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
