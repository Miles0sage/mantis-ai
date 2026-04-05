"""Standalone task planner for Mantis AI.

No external dependencies — stdlib only (re, dataclasses, pathlib).
Ported from ai-factory/planner.py with AI Factory imports removed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from pathlib import Path

ARCHITECTURE_KEYWORDS = {
    "architecture",
    "orchestrator",
    "engine",
    "system",
    "platform",
    "framework",
    "refactor",
}

TASK_PATTERNS = {
    "test_writing": r"(?i)\b(?:write|add|create|generate|update|run|fix)\b[^\n.]{0,80}\b(?:test|tests|pytest|jest|spec|coverage|tdd|unit test)\b|\b(?:pytest|jest)\b",
    "refactor": r"(?i)\b(refactor|clean up|simplify|extract|reorganize|rename)\b",
    "boilerplate": r"(?i)\b(scaffold|boilerplate|template|landing page|starter|hello world)\b",
    "bug_fix": r"(?i)\b(fix|bug|error|broken|crash|failing|issue|debug)\b",
    "feature": r"(?i)\b(add|implement|build|feature|integrate|wire|connect|write|create|generate|make|develop|update)\b",
    "docs": r"(?i)\b(document|docstring|readme|comment|explain|jsdoc|describe)\b",
    "review": r"(?i)\b(review|audit|check|analyze|inspect|evaluate)\b",
    "research": r"(?i)\b(research|find|search|compare|investigate|look up)\b",
    "devops": r"(?i)\b(deploy|docker|ci\/cd|pipeline|kubernetes|terraform|nginx|helm|k8s)\b",
    "data": r"(?i)\b(query|sql|migration|schema|database|csv|etl|transform)\b",
}


@dataclass(slots=True)
class PlannedTask:
    title: str
    prompt: str
    task_type: str
    file_targets: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    estimated_scope: str = "atomic"
    parallel_group: str = "default"
    needs_escalation: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ExecutionPlan:
    task_type: str
    complexity: str
    can_run_in_parallel: bool
    needs_escalation: bool
    tasks: list[PlannedTask] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "complexity": self.complexity,
            "can_run_in_parallel": self.can_run_in_parallel,
            "needs_escalation": self.needs_escalation,
            "tasks": [task.to_dict() for task in self.tasks],
        }


def _extract_file_targets(prompt: str) -> list[str]:
    """Find file paths with common extensions in the prompt."""
    pattern = re.compile(
        r"(?<!\w)([\w./-]+\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|sql|sh))(?!\w)"
    )
    seen: list[str] = []
    for match in pattern.findall(prompt):
        if match not in seen:
            seen.append(match)
    return seen


def classify_task(prompt: str) -> str:
    """Classify a prompt into a task type using regex pattern matching.

    Counts matches per category; highest count wins.
    Returns 'unknown' when no patterns match.
    """
    scores: dict[str, int] = {}
    for task_type, pattern in TASK_PATTERNS.items():
        matches = re.findall(pattern, prompt)
        if matches:
            scores[task_type] = len(matches)
    if not scores:
        return "unknown"
    return max(scores.items(), key=lambda item: item[1])[0]


def _extract_postconditions(chunk: str, file_targets: list[str]) -> list[str]:
    """Extract explicit success conditions from the task text."""
    postconditions: list[str] = []

    for target in file_targets:
        postconditions.append(f"file exists: {target}")

    class_patterns = [
        re.compile(r"\b([A-Z][A-Za-z0-9_]*) class\b"),
        re.compile(r"\bclass ([A-Z][A-Za-z0-9_]*)\b"),
    ]
    seen_classes: list[str] = []
    for pattern in class_patterns:
        for match in pattern.findall(chunk):
            if match not in seen_classes:
                seen_classes.append(match)
                postconditions.append(f"class exists: {match}")

    seen_functions: list[str] = []
    for match in re.findall(r"\b([a-z_][A-Za-z0-9_]*) function\b", chunk):
        if match not in seen_functions:
            seen_functions.append(match)
            postconditions.append(f"function exists: {match}")
    for match in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", chunk):
        if match not in seen_functions and match not in {"__init__", "allow", "available"}:
            seen_functions.append(match)
            postconditions.append(f"function exists: {match}")

    method_block = re.search(r"methods? ([^.]+)", chunk, flags=re.IGNORECASE)
    if method_block:
        block = method_block.group(1)
        method_names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|,|$)", block)
        seen_methods: list[str] = []
        for match in method_names:
            if match not in seen_methods:
                seen_methods.append(match)
                postconditions.append(f"method exists: {match}")

    if re.search(r"\b(?:create|write|add).*\btest", chunk, flags=re.IGNORECASE):
        postconditions.append("tests added or updated")
    if re.search(r"\b(?:run|pass).*\bpytest|\btests?\b", chunk, flags=re.IGNORECASE):
        postconditions.append("verification passes")

    deduped: list[str] = []
    for item in postconditions:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _split_atomic_chunks(prompt: str) -> list[tuple[str, bool]]:
    """Split a prompt on connectors ('and then', 'then', ' and ').

    Returns a list of (chunk_text, depends_on_previous) tuples.
    Sequential connectors ('then', 'and then') set depends_on_previous=True.
    Parallel connectors ('and') set depends_on_previous=False.
    """
    normalized = re.sub(r"\s+", " ", prompt.strip())
    split_pattern = re.compile(
        r"(\b(?:and then|then| and |, then )\b)", re.IGNORECASE
    )
    parts = [part for part in split_pattern.split(normalized) if part]
    cleaned: list[tuple[str, bool]] = []
    depends_on_previous = False

    for part in parts:
        stripped = part.strip(" ,.")
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered in {"then", "and then"}:
            depends_on_previous = True
            continue
        if lowered == "and":
            depends_on_previous = False
            continue
        cleaned.append((stripped, depends_on_previous))
        depends_on_previous = False

    # Merge back any chunk that has no primary action verb — it's a complement
    # or modifier, not a standalone task. This handles both short fragments
    # ("type hints") and longer ones ("docstrings to /tmp/math_utils.py").
    # "use" and "include" are intentionally excluded: they modify a task, not drive one.
    _ACTION = re.compile(
        r"\b(write|add|implement|build|create|fix|refactor|run|test|update|generate|"
        r"remove|delete|deploy|read|check|find|make)\b",
        re.IGNORECASE,
    )
    _CONTINUATION = re.compile(
        r"^\b(reply|return|report|tell|summarize|answer|list|show|give)\b",
        re.IGNORECASE,
    )
    merged: list[tuple[str, bool]] = []
    for chunk, dep in cleaned:
        if merged and (not _ACTION.search(chunk) or _CONTINUATION.search(chunk)):
            # No primary action verb → append as continuation of previous chunk
            prev_chunk, prev_dep = merged[-1]
            merged[-1] = (prev_chunk + " and " + chunk, prev_dep)
        else:
            merged.append((chunk, dep))

    return merged or [(normalized, False)]


def _infer_complexity(
    prompt: str,
    file_targets: list[str],
    atomic_chunks: list[tuple[str, bool]],
) -> str:
    """Infer task complexity as 'high', 'medium', or 'low'."""
    lowered = prompt.lower()
    if len(file_targets) >= 3 or len(atomic_chunks) >= 3:
        return "high"
    if any(keyword in lowered for keyword in ARCHITECTURE_KEYWORDS):
        return "high"
    if len(file_targets) == 2 or len(atomic_chunks) == 2:
        return "medium"
    return "low"


def _make_subtask(
    chunk: str,
    depends_on_previous: bool,
    base_task_type: str,
    file_targets: list[str],
    index: int,
    total: int,
) -> PlannedTask:
    """Build a single PlannedTask from an atomic chunk."""
    chunk_files = [
        target
        for target in file_targets
        if re.search(rf"(?<![\w./-]){re.escape(target)}(?![\w./-])", chunk)
    ]
    if not chunk_files and total == 1:
        chunk_files = list(file_targets)

    title = chunk[:72] if len(chunk) <= 72 else chunk[:69] + "..."
    estimated_scope = "atomic"
    needs_escalation = False

    if len(chunk_files) >= 3 or any(
        keyword in chunk.lower() for keyword in ARCHITECTURE_KEYWORDS
    ):
        estimated_scope = "architectural"
        needs_escalation = True
    elif len(chunk_files) >= 2:
        estimated_scope = "multi_file"

    parallel_group = (
        f"group-{index}"
        if total > 1 and not needs_escalation and not depends_on_previous
        else "serial"
    )

    postconditions = _extract_postconditions(chunk, chunk_files)

    return PlannedTask(
        title=title,
        prompt=chunk,
        task_type=classify_task(chunk) or base_task_type,
        file_targets=chunk_files,
        postconditions=postconditions,
        estimated_scope=estimated_scope,
        parallel_group=parallel_group,
        needs_escalation=needs_escalation,
    )


def build_execution_plan(
    prompt: str, cwd: str | None = None
) -> ExecutionPlan:
    """Build a full execution plan from a natural-language prompt.

    Classifies the task, extracts file targets, splits into atomic chunks,
    infers complexity, and wires up dependencies between sequential tasks.
    """
    resolved_task_type = classify_task(prompt)
    file_targets = _extract_file_targets(prompt)
    atomic_chunks = _split_atomic_chunks(prompt)
    complexity = _infer_complexity(prompt, file_targets, atomic_chunks)

    tasks = [
        _make_subtask(
            chunk,
            depends_on_previous,
            resolved_task_type,
            file_targets,
            index,
            len(atomic_chunks),
        )
        for index, (chunk, depends_on_previous) in enumerate(atomic_chunks, start=1)
    ]

    if cwd:
        repo_name = Path(cwd).name
        for task in tasks:
            task.prompt = f"[repo:{repo_name}] {task.prompt}"

    needs_escalation = complexity == "high" or any(
        task.needs_escalation for task in tasks
    )
    can_run_in_parallel = len(tasks) > 1 and not needs_escalation

    if len(tasks) > 1:
        previous_title: str | None = None
        for task in tasks:
            if previous_title and task.parallel_group == "serial":
                task.dependencies.append(previous_title)
            previous_title = task.title

    return ExecutionPlan(
        task_type=resolved_task_type,
        complexity=complexity,
        can_run_in_parallel=can_run_in_parallel,
        needs_escalation=needs_escalation,
        tasks=tasks,
    )
