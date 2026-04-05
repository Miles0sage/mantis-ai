"""Tests for the 3-tier quality cascade (compile → test → semantic)."""

from __future__ import annotations

import asyncio
import os
import pytest

from mantis.core.quality_gate import (
    _check_compilation,
    _check_tests,
    _javascript_interface_findings,
    _python_interface_findings,
    _python_placeholder_findings,
    verify_cascade,
    verify_output,
    execute_with_quality_gate,
)


# ---------------------------------------------------------------------------
# Tier 1 — Compilation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compile_python_clean(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def hello():\n    return 42\n")
    result = await _check_compilation([str(f)])
    assert result is not None
    passed, output = result
    assert passed is True
    assert "clean" in output


@pytest.mark.asyncio
async def test_compile_python_syntax_error(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def oops(\n")  # syntax error
    result = await _check_compilation([str(f)])
    assert result is not None
    passed, output = result
    assert passed is False


@pytest.mark.asyncio
async def test_compile_no_recognized_files():
    result = await _check_compilation(["readme.md", "data.json"])
    assert result is None  # no compilable files → skip tier


@pytest.mark.asyncio
async def test_compile_empty_targets():
    result = await _check_compilation([])
    assert result is None


# ---------------------------------------------------------------------------
# Tier 2 — Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_tests_no_test_files(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    result = await _check_tests([str(f)], cwd=str(tmp_path))
    assert result is None  # no test files → skip tier


@pytest.mark.asyncio
async def test_check_tests_scopes_temp_targets_without_cwd(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    result = await _check_tests([str(f)])
    assert result is None


@pytest.mark.asyncio
async def test_check_tests_python_passing(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    src = tmp_path / "app.py"
    src.write_text("def add(a, b): return a + b\n")
    test = tmp_path / "test_app.py"
    test.write_text("from app import add\ndef test_add():\n    assert add(1, 2) == 3\n")
    result = await _check_tests([str(src)], cwd=str(tmp_path))
    assert result is not None
    passed, output = result
    assert passed is True


@pytest.mark.asyncio
async def test_check_tests_python_failing(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    src = tmp_path / "app.py"
    src.write_text("def add(a, b): return a - b\n")  # deliberately wrong
    test = tmp_path / "test_app.py"
    test.write_text("from app import add\ndef test_add():\n    assert add(1, 2) == 3\n")
    result = await _check_tests([str(src)], cwd=str(tmp_path))
    assert result is not None
    passed, output = result
    assert passed is False


@pytest.mark.asyncio
async def test_check_tests_no_js_config(tmp_path):
    f = tmp_path / "app.ts"
    f.write_text("export const x = 1;\n")
    result = await _check_tests([str(f)], cwd=str(tmp_path))
    assert result is None  # no jest/vitest config → skip


# ---------------------------------------------------------------------------
# Full cascade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cascade_compile_fail_scores_low(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def oops(\n")
    score, reason = await verify_cascade("feature", "wrote some code", [str(f)])
    assert score == 0.3
    assert "Tier 1 FAIL" in reason


@pytest.mark.asyncio
async def test_cascade_compile_pass_boosts_semantic(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def hello():\n    return 42\n")
    # Semantic would score 0.5 for "feature" with no defs in output,
    # but compile pass floors it at 0.8
    score, reason = await verify_cascade("feature", "done, wrote it", [str(f)])
    assert score >= 0.8
    assert "compile: PASS" in reason


@pytest.mark.asyncio
async def test_cascade_no_files_falls_back_to_semantic():
    score, reason = await verify_cascade("feature", "def hello(): pass\ndef world(): pass")
    # No file targets → pure semantic → 0.85 for multiple defs
    assert score == 0.85


@pytest.mark.asyncio
async def test_cascade_test_fail_scores_04(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    src = tmp_path / "app.py"
    src.write_text("def add(a, b): return a - b\n")
    test = tmp_path / "test_app.py"
    test.write_text("from app import add\ndef test_add():\n    assert add(1, 2) == 3\n")
    score, reason = await verify_cascade("feature", "wrote it", [str(src)], cwd=str(tmp_path))
    assert score == 0.4
    assert "Tier 2 FAIL" in reason


def test_python_placeholder_findings_detects_incomplete_defs(tmp_path):
    f = tmp_path / "placeholder.py"
    f.write_text(
        "def todo():\n    pass\n\n"
        "def later():\n    raise NotImplementedError()\n\n"
        "class Pending:\n    pass\n",
        encoding="utf-8",
    )
    findings = _python_placeholder_findings(str(f))
    assert any("todo" in finding for finding in findings)
    assert any("later" in finding for finding in findings)
    assert any("Pending" in finding for finding in findings)


def test_python_interface_findings_detect_missing_contract(tmp_path):
    f = tmp_path / "token_bucket.py"
    f.write_text(
        "class WrongName:\n"
        "    def available(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    findings = _python_interface_findings(
        "Create token_bucket.py implementing a TokenBucket class with methods __init__, allow(tokens: int = 1), available().",
        [str(f)],
    )
    assert any("TokenBucket" in finding for finding in findings)
    assert any("allow" in finding for finding in findings)


def test_python_interface_findings_accepts_contract_across_multiple_targets(tmp_path):
    a = tmp_path / "token_bucket.py"
    b = tmp_path / "helpers.py"
    a.write_text(
        "class TokenBucket:\n"
        "    def __init__(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    b.write_text(
        "def allow(tokens: int = 1):\n"
        "    return True\n\n"
        "def available():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    findings = _python_interface_findings(
        "Create token_bucket.py implementing a TokenBucket class with methods __init__, allow(tokens: int = 1), available().",
        [str(a), str(b)],
    )
    assert findings == []


def test_javascript_interface_findings_detect_missing_contract(tmp_path):
    f = tmp_path / "auth.ts"
    f.write_text(
        "export class WrongName {}\n"
        "export function available() { return true; }\n",
        encoding="utf-8",
    )
    findings = _javascript_interface_findings(
        "Create auth.ts implementing an AuthService class and createSession function.",
        [str(f)],
    )
    assert any("AuthService" in finding for finding in findings)
    assert any("createSession" in finding for finding in findings)


def test_javascript_interface_findings_accepts_contract_across_multiple_targets(tmp_path):
    a = tmp_path / "auth.ts"
    b = tmp_path / "session.ts"
    a.write_text("export class AuthService {}\n", encoding="utf-8")
    b.write_text("export function createSession() { return true; }\n", encoding="utf-8")
    findings = _javascript_interface_findings(
        "Create auth.ts implementing an AuthService class and createSession function.",
        [str(a), str(b)],
    )
    assert findings == []


@pytest.mark.asyncio
async def test_cascade_incomplete_python_impl_fails_before_semantic_pass(tmp_path):
    f = tmp_path / "placeholder.py"
    f.write_text(
        "def todo():\n    pass\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade("feature", "written and saved", [str(f)])
    assert score == 0.45
    assert "Tier 2.5 FAIL" in reason


@pytest.mark.asyncio
async def test_cascade_missing_python_interface_fails_before_semantic_pass(tmp_path):
    f = tmp_path / "token_bucket.py"
    f.write_text(
        "class WrongName:\n"
        "    def available(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved",
        [str(f)],
        prompt="Create token_bucket.py implementing a TokenBucket class with methods __init__, allow(tokens: int = 1), available().",
    )
    assert score == 0.46
    assert "Tier 2.6 FAIL" in reason


@pytest.mark.asyncio
async def test_cascade_missing_javascript_interface_fails_before_semantic_pass(tmp_path):
    f = tmp_path / "auth.ts"
    f.write_text(
        "export class WrongName {}\n"
        "export function available() { return true; }\n",
        encoding="utf-8",
    )
    score, reason = await verify_cascade(
        "feature",
        "written and saved",
        [str(f)],
        prompt="Create auth.ts implementing an AuthService class and createSession function.",
    )
    assert score == 0.47
    assert "Tier 2.7 FAIL" in reason


@pytest.mark.asyncio
async def test_cascade_allows_multifile_interface_contract_when_targets_together_match(tmp_path):
    a = tmp_path / "auth.ts"
    b = tmp_path / "session.ts"
    a.write_text("export class AuthService {}\n", encoding="utf-8")
    b.write_text("export function createSession() { return true; }\n", encoding="utf-8")
    score, reason = await verify_cascade(
        "feature",
        "written and saved",
        [str(a), str(b)],
        prompt="Create auth.ts implementing an AuthService class and createSession function.",
    )
    assert score >= 0.8
    assert "Tier 2.7 FAIL" not in reason


# ---------------------------------------------------------------------------
# execute_with_quality_gate with file_targets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gate_with_file_targets_compile_clean(tmp_path):
    f = tmp_path / "good.py"
    f.write_text("def foo(): return 1\n")

    async def _fn(prompt: str) -> str:
        return "wrote foo function to good.py"

    result = await execute_with_quality_gate(
        _fn, "write foo", "feature", file_targets=[str(f)]
    )
    assert result.success is True
    assert result.score >= 0.8


@pytest.mark.asyncio
async def test_gate_without_file_targets_is_semantic_only():
    async def _fn(prompt: str) -> str:
        return "def test_a(): pass\ndef test_b(): pass"

    result = await execute_with_quality_gate(_fn, "write tests", "test_writing")
    # No file_targets → semantic only → 0.8 for test defs
    assert result.success is True
    assert result.score >= 0.8
