"""
Standalone quality gate for MantisAI.

Pattern: execute -> verify -> self-correct -> accept/fail.
No external dependencies — stdlib + typing only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
GOOD: float = 0.8
ACCEPTABLE: float = 0.6
FAIL: float = 0.0  # anything below ACCEPTABLE

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "good": GOOD,
    "acceptable": ACCEPTABLE,
}


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class QualityResult:
    """Immutable result of a quality-gated execution."""

    success: bool
    output: str
    score: float
    attempts: int
    self_corrected: bool


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify_output(
    task_type: str,
    output: str,
    cwd: Optional[str] = None,
) -> Tuple[float, str]:
    """Score *output* based on heuristics for *task_type*.

    Returns ``(score, reason)`` where score is in ``[0.0, 1.0]``.
    """
    if not output or len(output.strip()) < 20:
        return 0.1, "Output is empty or too short (< 20 chars)"

    task = task_type.lower()

    if task == "test_writing":
        if "def test_" in output:
            return 0.8, "Contains test function definitions"
        return 0.5, "test_writing output lacks 'def test_' definitions"

    if task == "bug_fix":
        if "<<<<<<< SEARCH" in output or "SEARCH/REPLACE" in output:
            return 0.8, "Contains SEARCH/REPLACE block"
        return 0.5, "bug_fix output lacks SEARCH/REPLACE block"

    if task == "feature":
        # Tool-using agents write files; the chat reply is just a confirmation.
        # Detect tool-completion language and score GOOD so we never self-correct.
        tool_done_phrases = ("written", "created", "saved", "updated", "added", "generated", "wrote")
        if any(p in output.lower() for p in tool_done_phrases):
            return 0.85, "Agent confirmed tool-based file operation"
        definition_count = output.count("def ") + output.count("class ")
        if definition_count >= 2:
            return 0.85, "Contains multiple function/class definitions"
        if definition_count == 1 and len(output.strip()) >= 120:
            return 0.8, "Contains a substantial function/class implementation"
        if definition_count == 1:
            return 0.7, "Contains a single function or class definition"
        return 0.5, "feature output lacks def/class definitions"

    if task == "docs":
        if len(output) > 100:
            return 0.8, "Documentation has sufficient length"
        return 0.5, "Documentation is short (<=100 chars)"

    return 0.5, f"Default score for unknown task type '{task_type}'"


# ---------------------------------------------------------------------------
# Core gate
# ---------------------------------------------------------------------------
async def execute_with_quality_gate(
    execute_fn: Callable[[str], Awaitable[str]],
    prompt: str,
    task_type: str,
    cwd: Optional[str] = None,
    max_attempts: int = 2,
) -> QualityResult:
    """Run *execute_fn*, verify quality, self-correct if needed.

    * ``>= 0.8``  — accept immediately.
    * ``0.6–0.8`` — append feedback and retry (if attempts remain).
    * ``< 0.6``   — fail.

    Returns the best :class:`QualityResult` seen across all attempts.
    """
    best: Optional[QualityResult] = None

    current_prompt = prompt
    for attempt in range(1, max_attempts + 1):
        output = await execute_fn(current_prompt)
        score, reason = verify_output(task_type, output, cwd=cwd)
        self_corrected = attempt > 1

        result = QualityResult(
            success=score >= ACCEPTABLE,
            output=output,
            score=score,
            attempts=attempt,
            self_corrected=self_corrected,
        )

        if best is None or result.score >= best.score:
            best = result

        # Accept immediately on GOOD
        if score >= GOOD:
            return QualityResult(
                success=True,
                output=output,
                score=score,
                attempts=attempt,
                self_corrected=self_corrected,
            )

        # Self-correct if ACCEPTABLE and attempts remain
        if score >= ACCEPTABLE and attempt < max_attempts:
            current_prompt = (
                f"{prompt}\n\n"
                f"[SELF-CORRECTION FEEDBACK] Previous attempt scored {score:.2f}. "
                f"Reason: {reason}. Please improve the output."
            )
            continue

        # Below ACCEPTABLE — no point retrying
        if score < ACCEPTABLE:
            break

    # Exhausted attempts — return best result
    assert best is not None
    return best


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_quality_gate(
    thresholds: Optional[Dict[str, float]] = None,
) -> Callable[
    [Callable[[str], Awaitable[str]], str, str, Optional[str], int],
    Awaitable[QualityResult],
]:
    """Return a quality-gate callable pre-configured with *thresholds*.

    Usage::

        gate = create_quality_gate({"good": 0.9, "acceptable": 0.7})
        result = await gate(my_fn, "write tests", "test_writing")
    """
    merged = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    good = merged["good"]
    acceptable = merged["acceptable"]

    async def _gate(
        execute_fn: Callable[[str], Awaitable[str]],
        prompt: str,
        task_type: str,
        cwd: Optional[str] = None,
        max_attempts: int = 2,
    ) -> QualityResult:
        current_prompt = prompt
        best: Optional[QualityResult] = None

        for attempt in range(1, max_attempts + 1):
            output = await execute_fn(current_prompt)
            score, reason = verify_output(task_type, output, cwd=cwd)
            self_corrected = attempt > 1

            result = QualityResult(
                success=score >= acceptable,
                output=output,
                score=score,
                attempts=attempt,
                self_corrected=self_corrected,
            )

            if best is None or result.score >= best.score:
                best = result

            if score >= good:
                return QualityResult(
                    success=True,
                    output=output,
                    score=score,
                    attempts=attempt,
                    self_corrected=self_corrected,
                )

            if score >= acceptable and attempt < max_attempts:
                current_prompt = (
                    f"{prompt}\n\n"
                    f"[SELF-CORRECTION FEEDBACK] Previous attempt scored {score:.2f}. "
                    f"Reason: {reason}. Please improve the output."
                )
                continue

            if score < acceptable:
                break

        assert best is not None
        return best

    return _gate
