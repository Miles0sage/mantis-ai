"""
Standalone quality gate for MantisAI.

Pattern: execute -> verify -> self-correct -> accept/fail.

Quality cascade (3 tiers):
  Tier 1 — Compile (tsc --noEmit / python -m py_compile)
  Tier 2 — Test    (pytest / vitest, only if test files exist nearby)
  Tier 3 — Semantic (heuristic scoring: defs, tool phrases, length)
"""
from __future__ import annotations

import asyncio
import ast
import glob
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Awaitable, Dict, List, Optional, Tuple


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
    lowered = output.lower()

    tool_done_phrases = ("written", "created", "saved", "updated", "added", "generated", "wrote")
    verification_phrases = ("tests pass", "tests passed", "pytest passed", "verified", "verification passed")
    has_completion_signal = any(p in lowered for p in tool_done_phrases) or any(p in lowered for p in verification_phrases)

    if task == "test_writing":
        if "def test_" in output:
            return 0.8, "Contains test function definitions"
        if has_completion_signal:
            return 0.8, "Agent confirmed test-writing completion"
        return 0.5, "test_writing output lacks 'def test_' definitions"

    if task == "bug_fix":
        if "<<<<<<< SEARCH" in output or "SEARCH/REPLACE" in output:
            return 0.8, "Contains SEARCH/REPLACE block"
        if has_completion_signal:
            return 0.8, "Agent confirmed bug-fix completion"
        return 0.5, "bug_fix output lacks SEARCH/REPLACE block"

    if task == "feature":
        # Tool-using agents write files; the chat reply is just a confirmation.
        # Detect tool-completion language and score GOOD so we never self-correct.
        if has_completion_signal:
            return 0.85, "Agent confirmed tool-based file operation"
        definition_count = output.count("def ") + output.count("class ")
        if definition_count >= 2:
            return 0.85, "Contains multiple function/class definitions"
        if definition_count == 1 and len(output.strip()) >= 120:
            return 0.8, "Contains a substantial function/class implementation"
        if definition_count == 1:
            return 0.7, "Contains a single function or class definition"
        return 0.5, "feature output lacks def/class definitions"

    if task == "refactor":
        if has_completion_signal:
            return 0.85, "Agent confirmed refactor completion"
        if "def " in output or "class " in output:
            return 0.75, "Refactor output includes concrete code structure"
        return 0.5, "refactor output lacks concrete completion evidence"

    if task == "docs":
        if len(output) > 100:
            return 0.8, "Documentation has sufficient length"
        return 0.5, "Documentation is short (<=100 chars)"

    if has_completion_signal:
        return 0.7, f"Agent confirmed completion for task type '{task_type}'"
    return 0.5, f"Default score for unknown task type '{task_type}'"


def _python_placeholder_findings(file_path: str) -> list[str]:
    """Detect obviously incomplete Python implementations in target files."""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file_path)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    findings: list[str] = []

    def _record(kind: str, name: str) -> None:
        findings.append(f"{kind} '{name}' looks incomplete")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body or []
            if len(body) == 1:
                stmt = body[0]
                if isinstance(stmt, ast.Pass):
                    _record("function", node.name)
                elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
                    _record("function", node.name)
                elif isinstance(stmt, ast.Raise):
                    exc = stmt.exc
                    if isinstance(exc, ast.Call) and getattr(exc.func, "id", None) == "NotImplementedError":
                        _record("function", node.name)
        elif isinstance(node, ast.ClassDef):
            body = node.body or []
            real_members = [
                stmt for stmt in body
                if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str))
            ]
            if len(real_members) == 1:
                stmt = real_members[0]
                if isinstance(stmt, ast.Pass):
                    _record("class", node.name)
                elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
                    _record("class", node.name)

    return findings


def _extract_required_classes(prompt: str) -> list[str]:
    seen: list[str] = []
    patterns = [
        re.compile(r"\b([A-Z][A-Za-z0-9_]*) class\b"),
        re.compile(r"\bclass ([A-Z][A-Za-z0-9_]*)\b"),
    ]
    for pattern in patterns:
        for match in pattern.findall(prompt):
            if match not in seen:
                seen.append(match)
    return seen


def _extract_required_functions(prompt: str) -> list[str]:
    seen: list[str] = []
    for match in re.findall(r"\b([a-z_][A-Za-z0-9_]*) function\b", prompt):
        if match not in seen:
            seen.append(match)
    for match in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", prompt):
        if match not in seen and match not in {"__init__", "allow", "available"}:
            seen.append(match)
    return seen


def _extract_required_methods(prompt: str) -> list[str]:
    seen: list[str] = []
    method_block = re.search(r"methods? ([^.]+)", prompt, flags=re.IGNORECASE)
    if method_block:
        block = method_block.group(1)
        for match in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", block):
            if match not in seen:
                seen.append(match)
        if not seen:
            for match in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:,|$)", block):
                if match not in seen:
                    seen.append(match)
    return seen


def _python_interface_findings(prompt: str, file_targets: list[str]) -> list[str]:
    if not prompt:
        return []

    required_classes = _extract_required_classes(prompt)
    required_functions = _extract_required_functions(prompt)
    required_methods = _extract_required_methods(prompt)
    if not (required_classes or required_functions or required_methods):
        return []

    python_targets = [target for target in file_targets if target.endswith(".py")]
    if not python_targets:
        return []

    contents: list[tuple[str, str]] = []
    for target in python_targets:
        try:
            contents.append((target, Path(target).read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    if not contents:
        return []

    combined_content = "\n".join(content for _, content in contents)
    target_label = ", ".join(path for path, _ in contents[:3])

    findings: list[str] = []
    for class_name in required_classes:
        if class_name not in combined_content:
            findings.append(f"missing class {class_name} across targets ({target_label})")
    for func_name in required_functions:
        if f"def {func_name}(" not in combined_content and f"class {func_name}" not in combined_content:
            findings.append(f"missing function {func_name} across targets ({target_label})")
    for method_name in required_methods:
        if f"def {method_name}(" not in combined_content:
            findings.append(f"missing method {method_name} across targets ({target_label})")
    return findings


def _javascript_interface_findings(prompt: str, file_targets: list[str]) -> list[str]:
    if not prompt:
        return []

    required_classes = _extract_required_classes(prompt)
    required_functions = _extract_required_functions(prompt)
    if not (required_classes or required_functions):
        return []

    js_targets = [
        target
        for target in file_targets
        if target.endswith((".js", ".jsx", ".ts", ".tsx"))
    ]
    if not js_targets:
        return []

    contents: list[tuple[str, str]] = []
    for target in js_targets:
        try:
            contents.append((target, Path(target).read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    if not contents:
        return []

    combined_content = "\n".join(content for _, content in contents)
    target_label = ", ".join(path for path, _ in contents[:3])

    findings: list[str] = []
    for class_name in required_classes:
        class_patterns = [
            rf"\bclass\s+{re.escape(class_name)}\b",
            rf"\bexport\s+class\s+{re.escape(class_name)}\b",
        ]
        if not any(re.search(pattern, combined_content) for pattern in class_patterns):
            findings.append(f"missing class {class_name} across targets ({target_label})")

    for func_name in required_functions:
        function_patterns = [
            rf"\bfunction\s+{re.escape(func_name)}\s*\(",
            rf"\bexport\s+function\s+{re.escape(func_name)}\s*\(",
            rf"\bconst\s+{re.escape(func_name)}\s*=\s*(?:async\s*)?\(",
            rf"\bexport\s+const\s+{re.escape(func_name)}\s*=\s*(?:async\s*)?\(",
            rf"\bconst\s+{re.escape(func_name)}\s*=\s*(?:async\s*)?[^=]+=>",
            rf"\bexport\s+const\s+{re.escape(func_name)}\s*=\s*(?:async\s*)?[^=]+=>",
        ]
        if not any(re.search(pattern, combined_content) for pattern in function_patterns):
            findings.append(f"missing function {func_name} across targets ({target_label})")

    return findings


# ---------------------------------------------------------------------------
# Tier 1 — Compilation check
# ---------------------------------------------------------------------------
async def _check_compilation(
    file_targets: List[str],
    cwd: Optional[str] = None,
) -> Optional[Tuple[bool, str]]:
    """Run a language-specific compiler. Returns (passed, output) or None if N/A."""
    ts_files = [f for f in file_targets if f.endswith((".ts", ".tsx"))]
    py_files = [f for f in file_targets if f.endswith(".py")]

    if ts_files:
        ts_dir = str(Path(ts_files[0]).parent) if ts_files[0] != ts_files[0] else (cwd or ".")
        # Find the nearest directory that has a tsconfig.json
        check_dir = Path(cwd or ".") if cwd else Path(ts_files[0]).parent
        try:
            proc = await asyncio.create_subprocess_shell(
                f"cd {check_dir} && npx tsc --noEmit 2>&1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = (stdout + stderr).decode("utf-8").strip()
            if proc.returncode == 0 or "error TS" not in output:
                return (True, "tsc: clean")
            return (False, output[:3000])
        except (asyncio.TimeoutError, OSError):
            return None  # can't run tsc — skip tier

    if py_files:
        errors = []
        for f in py_files[:5]:  # check up to 5 files
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python3", "-m", "py_compile", f,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode != 0:
                    errors.append((stderr + stdout).decode("utf-8").strip())
            except (asyncio.TimeoutError, OSError):
                continue
        if errors:
            return (False, "\n".join(errors[:3]))
        return (True, "py_compile: clean")

    return None  # no recognized compiled language


# ---------------------------------------------------------------------------
# Tier 2 — Test runner check
# ---------------------------------------------------------------------------
async def _check_tests(
    file_targets: List[str],
    cwd: Optional[str] = None,
) -> Optional[Tuple[bool, str]]:
    """Run tests if test files exist near the targets. Returns (passed, output) or None."""
    if cwd:
        work_dir = cwd
    elif file_targets:
        target_dirs = []
        for target in file_targets:
            parent = str(Path(target).resolve().parent)
            if parent not in target_dirs:
                target_dirs.append(parent)
        work_dir = target_dirs[0] if len(target_dirs) == 1 else str(Path(os.path.commonpath(target_dirs)))
    else:
        work_dir = "."

    ts_files = [f for f in file_targets if f.endswith((".ts", ".tsx", ".js", ".jsx"))]
    py_files = [f for f in file_targets if f.endswith(".py")]

    if py_files:
        # Avoid infinite recursion: if we're already inside a pytest run, skip
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return None
        # Check if pytest is available and test files exist under the scoped work dir.
        test_files = glob.glob(os.path.join(work_dir, "**/test_*.py"), recursive=True)
        if not test_files:
            return None  # no tests to run
        try:
            proc = await asyncio.create_subprocess_shell(
                f"cd {work_dir} && python3 -m pytest -q --tb=short -x "
                f"-o asyncio_default_fixture_loop_scope=function 2>&1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = (stdout + stderr).decode("utf-8").strip()
            passed = proc.returncode == 0
            return (passed, output[-2000:] if len(output) > 2000 else output)
        except (asyncio.TimeoutError, OSError):
            return None

    if ts_files:
        # Check for vitest or jest config
        has_vitest = os.path.exists(os.path.join(work_dir, "vitest.config.ts")) or \
                     os.path.exists(os.path.join(work_dir, "vitest.config.js"))
        has_jest = os.path.exists(os.path.join(work_dir, "jest.config.ts")) or \
                   os.path.exists(os.path.join(work_dir, "jest.config.js"))
        if not has_vitest and not has_jest:
            return None
        cmd = "npx vitest run --reporter=verbose 2>&1" if has_vitest else "npx jest --passWithNoTests 2>&1"
        try:
            proc = await asyncio.create_subprocess_shell(
                f"cd {work_dir} && {cmd}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = (stdout + stderr).decode("utf-8").strip()
            passed = proc.returncode == 0
            return (passed, output[-2000:] if len(output) > 2000 else output)
        except (asyncio.TimeoutError, OSError):
            return None

    return None


# ---------------------------------------------------------------------------
# Cascade — all 3 tiers
# ---------------------------------------------------------------------------
async def verify_cascade(
    task_type: str,
    output: str,
    file_targets: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    prompt: str | None = None,
) -> Tuple[float, str]:
    """3-tier quality cascade: compile → test → semantic.

    Returns (score, reason). Compilation failure = 0.3, test failure = 0.4,
    both pass = max(semantic, 0.8).
    """
    tier_feedback: List[str] = []

    # Tier 1: Compilation
    if file_targets:
        compile_result = await _check_compilation(file_targets, cwd)
        if compile_result is not None:
            passed, compile_output = compile_result
            if not passed:
                return 0.3, f"Tier 1 FAIL (compile):\n{compile_output}"
            tier_feedback.append("compile: PASS")

    # Tier 2: Tests
    if file_targets:
        test_result = await _check_tests(file_targets, cwd)
        if test_result is not None:
            passed, test_output = test_result
            if not passed:
                return 0.4, f"Tier 2 FAIL (tests):\n{test_output}"
            tier_feedback.append("tests: PASS")

    # Tier 2.5: Python placeholder detection
    if file_targets:
        py_targets = [target for target in file_targets if target.endswith(".py")]
        placeholder_findings: list[str] = []
        for target in py_targets[:10]:
            placeholder_findings.extend(_python_placeholder_findings(target))
        if placeholder_findings:
            return 0.45, "Tier 2.5 FAIL (python completeness):\n" + "\n".join(placeholder_findings[:5])

    # Tier 2.6: Prompt-interface verification for Python tasks
    if file_targets and prompt:
        interface_findings = _python_interface_findings(prompt, file_targets)
        if interface_findings:
            return 0.46, "Tier 2.6 FAIL (python interface):\n" + "\n".join(interface_findings[:5])

    # Tier 2.7: Prompt-interface verification for JS/TS tasks
    if file_targets and prompt:
        js_interface_findings = _javascript_interface_findings(prompt, file_targets)
        if js_interface_findings:
            return 0.47, "Tier 2.7 FAIL (js/ts interface):\n" + "\n".join(js_interface_findings[:5])

    # Tier 3: Semantic (existing heuristic)
    semantic_score, semantic_reason = verify_output(task_type, output, cwd)

    # If hard checks (compile/tests) passed, floor at 0.8
    if tier_feedback:
        final_score = max(semantic_score, GOOD)
        return final_score, " | ".join(tier_feedback + [f"semantic: {semantic_reason}"])

    return semantic_score, semantic_reason


# ---------------------------------------------------------------------------
# Core gate
# ---------------------------------------------------------------------------
async def execute_with_quality_gate(
    execute_fn: Callable[[str], Awaitable[str]],
    prompt: str,
    task_type: str,
    cwd: Optional[str] = None,
    max_attempts: int = 2,
    file_targets: Optional[List[str]] = None,
) -> QualityResult:
    """Run *execute_fn*, verify quality, self-correct if needed.

    When *file_targets* are provided the 3-tier cascade runs
    (compile → test → semantic).  Without them, only the semantic
    heuristic fires (backwards-compatible).

    * ``>= 0.8``  — accept immediately.
    * ``0.6–0.8`` — append feedback and retry (if attempts remain).
    * ``< 0.6``   — fail.

    Returns the best :class:`QualityResult` seen across all attempts.
    """
    best: Optional[QualityResult] = None

    current_prompt = prompt
    for attempt in range(1, max_attempts + 1):
        output = await execute_fn(current_prompt)

        # Use cascade when file targets are available, else semantic only
        if file_targets:
            score, reason = await verify_cascade(task_type, output, file_targets, cwd, prompt=prompt)
        else:
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
