"""Tests for mantis.core.quality_gate — 8+ cases covering all paths."""
from __future__ import annotations

import asyncio
import pytest

from mantis.core.quality_gate import (
    ACCEPTABLE,
    GOOD,
    QualityResult,
    create_quality_gate,
    execute_with_quality_gate,
    verify_output,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_async_fn(output: str):
    """Return an async callable that always returns *output*."""
    async def _fn(prompt: str) -> str:
        return output
    return _fn


def _make_improving_fn(first: str, second: str):
    """Return an async callable that returns *first* on call 1, *second* on call 2."""
    calls: list[int] = []

    async def _fn(prompt: str) -> str:
        calls.append(1)
        return first if len(calls) == 1 else second
    return _fn


# ---------------------------------------------------------------------------
# verify_output
# ---------------------------------------------------------------------------

class TestVerifyOutput:
    def test_empty_output(self):
        score, reason = verify_output("feature", "")
        assert score == 0.1
        assert "empty" in reason.lower() or "short" in reason.lower()

    def test_short_output(self):
        score, _ = verify_output("feature", "tiny")
        assert score == 0.1

    def test_test_writing_with_test_def(self):
        code = "import pytest\n\ndef test_something():\n    assert True"
        score, _ = verify_output("test_writing", code)
        assert score == 0.8

    def test_bug_fix_with_search_replace(self):
        patch = "<<<<<<< SEARCH\nold code\n=======\nnew code\n>>>>>>> REPLACE"
        score, _ = verify_output("bug_fix", patch)
        assert score == 0.8

    def test_feature_with_class(self):
        code = "class MyFeature:\n    def __init__(self):\n        pass"
        score, _ = verify_output("feature", code)
        assert score == 0.85

    def test_feature_with_single_short_def_is_acceptable_not_good(self):
        code = "def helper():\n    return 1"
        score, _ = verify_output("feature", code)
        assert score == 0.7

    def test_feature_with_single_substantial_def_is_good(self):
        code = (
            "def build_feature(config, state):\n"
            "    result = []\n"
            "    for item in config:\n"
            "        result.append((item, state.get(item)))\n"
            "    return result\n"
        )
        score, _ = verify_output("feature", code)
        assert score == 0.8

    def test_docs_long(self):
        text = "This is documentation. " * 20
        score, _ = verify_output("docs", text)
        assert score == 0.8

    def test_unknown_task_type(self):
        score, reason = verify_output("unknown_type", "some reasonable output here, long enough to pass")
        assert score == 0.5
        assert "unknown" in reason.lower() or "default" in reason.lower()


# ---------------------------------------------------------------------------
# execute_with_quality_gate
# ---------------------------------------------------------------------------

class TestExecuteWithQualityGate:
    @pytest.mark.asyncio
    async def test_good_score_accepts_immediately(self):
        fn = _make_async_fn("def test_foo():\n    assert 1 + 1 == 2")
        result = await execute_with_quality_gate(fn, "write tests", "test_writing")
        assert result.success is True
        assert result.score >= GOOD
        assert result.attempts == 1
        assert result.self_corrected is False

    @pytest.mark.asyncio
    async def test_low_score_fails(self):
        fn = _make_async_fn("x")
        result = await execute_with_quality_gate(fn, "write code", "feature")
        assert result.success is False
        assert result.score < ACCEPTABLE
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_self_correction_on_acceptable(self):
        # First call returns acceptable (feature with no def/class -> 0.5 < ACCEPTABLE? No, 0.5 < 0.6)
        # Use feature with def -> 0.7 (acceptable, not good) on first, then good on second
        fn = _make_improving_fn(
            "def helper():\n    return 'not quite good enough yet'",  # feature -> 0.7
            "def test_thing():\n    assert True\ndef test_other():\n    pass",  # test_writing would be 0.8 but we use feature
        )
        # task_type=feature: first=0.7 (acceptable), second has def -> 0.7 still
        # Let's use test_writing: first lacks def test_ -> 0.5, which is < acceptable, so it fails immediately
        # Better: use feature, first=0.7, triggers retry, second also 0.7 -> returns best
        result = await execute_with_quality_gate(fn, "build feature", "feature", max_attempts=2)
        assert result.attempts == 2
        assert result.self_corrected is True

    @pytest.mark.asyncio
    async def test_max_attempts_respected(self):
        fn = _make_async_fn("def partial():\n    pass  # incomplete feature")
        result = await execute_with_quality_gate(fn, "build", "feature", max_attempts=3)
        # score=0.7 (acceptable, not good) -> retries up to max_attempts
        assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_below_acceptable_no_retry(self):
        fn = _make_async_fn("short")
        result = await execute_with_quality_gate(fn, "go", "feature", max_attempts=5)
        assert result.attempts == 1  # should not retry on < ACCEPTABLE


# ---------------------------------------------------------------------------
# QualityResult frozen
# ---------------------------------------------------------------------------

class TestQualityResult:
    def test_frozen(self):
        r = QualityResult(success=True, output="ok", score=0.9, attempts=1, self_corrected=False)
        with pytest.raises(AttributeError):
            r.success = False  # type: ignore[misc]

    def test_fields(self):
        r = QualityResult(success=False, output="out", score=0.3, attempts=2, self_corrected=True)
        assert r.success is False
        assert r.output == "out"
        assert r.score == 0.3
        assert r.attempts == 2
        assert r.self_corrected is True


# ---------------------------------------------------------------------------
# create_quality_gate factory
# ---------------------------------------------------------------------------

class TestCreateQualityGate:
    @pytest.mark.asyncio
    async def test_custom_thresholds(self):
        gate = create_quality_gate({"good": 0.9, "acceptable": 0.7})
        # class + method should now score above the acceptable floor but below custom good
        fn = _make_async_fn("class Foo:\n    def bar(self): pass")
        result = await gate(fn, "build", "feature")
        # score=0.85, good=0.9 -> not good; acceptable=0.7 -> acceptable, retries then returns
        assert result.score == 0.85

    @pytest.mark.asyncio
    async def test_default_thresholds(self):
        gate = create_quality_gate()
        fn = _make_async_fn("def test_ok():\n    assert True")
        result = await gate(fn, "write tests", "test_writing")
        assert result.success is True
        assert result.score >= 0.8
