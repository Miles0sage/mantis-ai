"""mantis/core/orchestrator.py — Orchestrator/Worker pattern for MantisAI.

For complex multi-file tasks:
  1. Orchestrator (DeepSeek/mid model) decomposes task → list of WorkerTasks
  2. Workers (cheap model) execute one atomic task each, in parallel where deps allow
  3. Assembly verifies all outputs exist and retries failures

This replaces the single-agent loop for high-complexity tasks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WorkerTask schema
# ---------------------------------------------------------------------------

@dataclass
class WorkerTask:
    id: str                          # e.g. "task-1"
    verb: str                        # "create", "edit", "test", "verify"
    target: str                      # file path or description
    spec: str                        # exact detailed spec for the worker
    depends_on: list[str] = field(default_factory=list)  # task IDs
    model_hint: str = "cheap"        # "cheap", "mid", "smart"
    result: Optional[str] = None     # filled after execution
    success: bool = False


# ---------------------------------------------------------------------------
# Worker prompt template
# ---------------------------------------------------------------------------

WORKER_SYSTEM_PROMPT = """You are a code execution engine. Your ONLY job is to execute the assigned task using tools.

Rules (CRITICAL):
- Call the appropriate tool ONCE with complete content
- Do NOT describe what you are doing
- Do NOT explain your reasoning
- Do NOT ask questions
- Do NOT write prose — only tool calls
- If creating a file, call write_file with the COMPLETE file content
- If editing a file, call edit_file with the exact change

Execute now."""


def _build_worker_prompt(task: WorkerTask) -> str:
    return f"""Task ID: {task.id}
Action: {task.verb}
Target: {task.target}
Spec: {task.spec}

Execute using tools. One tool call. Complete content. No explanations."""


# ---------------------------------------------------------------------------
# Orchestrator decomposition
# ---------------------------------------------------------------------------

DECOMPOSE_SYSTEM_PROMPT = """You are a task decomposition engine. Break down the user's request into atomic file-level tasks.

Output ONLY valid JSON — no prose, no markdown, no explanation.

JSON format:
{{
  "tasks": [
    {{
      "id": "task-1",
      "verb": "create",
      "target": "/path/to/file.ext",
      "spec": "detailed spec of exactly what to write — be specific about content, structure, features",
      "depends_on": [],
      "model_hint": "cheap"
    }}
  ]
}}

Rules:
- One task per file — never combine multiple files into one task
- spec must be detailed enough that a worker can execute it without asking questions
- depends_on lists task IDs that must complete first (e.g. style.css before index.html)
- model_hint: "cheap" for most tasks, "mid" for complex logic, "smart" for architecture
- verb: "create" (new file), "edit" (modify existing), "verify" (check output)
"""


async def decompose(
    prompt: str,
    model_adapter,
    cwd: str = ".",
) -> list[WorkerTask]:
    """Call the orchestrator model to decompose a complex task into WorkerTasks."""
    messages = [
        {
            "role": "user",
            "content": f"Working directory: {cwd}\n\nTask to decompose:\n{prompt}"
        }
    ]

    try:
        response = await model_adapter.chat(
            messages=messages,
            system_prompt=DECOMPOSE_SYSTEM_PROMPT,
            temperature=0.1,
        )
        content = response.get("content", "") if isinstance(response, dict) else str(response)

        # Extract JSON from response
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            logger.error(f"Orchestrator returned no JSON: {content[:200]}")
            return []

        data = json.loads(json_match.group())
        tasks_data = data.get("tasks", [])

        tasks = []
        for t in tasks_data:
            tasks.append(WorkerTask(
                id=t.get("id", f"task-{len(tasks)+1}"),
                verb=t.get("verb", "create"),
                target=t.get("target", ""),
                spec=t.get("spec", ""),
                depends_on=t.get("depends_on", []),
                model_hint=t.get("model_hint", "cheap"),
            ))

        logger.info(f"Orchestrator decomposed into {len(tasks)} tasks")
        return tasks

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Orchestrator decomposition failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Worker execution
# ---------------------------------------------------------------------------

async def _run_worker(
    task: WorkerTask,
    query_engine,
    cwd: str,
) -> WorkerTask:
    """Run a single worker task."""
    prompt = _build_worker_prompt(task)
    try:
        result = await query_engine.run_agentic(
            prompt,
            system_prompt=WORKER_SYSTEM_PROMPT,
        )
        task.result = result
        # Check if target file was actually created
        if task.verb == "create" and task.target:
            task.success = os.path.exists(task.target)
        else:
            task.success = True
        logger.info(f"Worker {task.id} {'succeeded' if task.success else 'FAILED'}: {task.target}")
    except Exception as e:
        logger.error(f"Worker {task.id} error: {e}")
        task.result = str(e)
        task.success = False
    return task


# ---------------------------------------------------------------------------
# Topological dispatcher
# ---------------------------------------------------------------------------

async def dispatch(
    tasks: list[WorkerTask],
    query_engine,
    cwd: str = ".",
    max_retries: int = 1,
) -> list[WorkerTask]:
    """Execute tasks respecting dependencies. Parallel where possible.

    Uses topological ordering: tasks with no pending deps run immediately.
    Failed tasks are retried up to max_retries times.
    """
    completed: dict[str, WorkerTask] = {}
    pending = {t.id: t for t in tasks}
    failed: list[WorkerTask] = []

    while pending:
        # Find tasks whose deps are all completed
        ready = [
            t for t in pending.values()
            if all(dep in completed for dep in t.depends_on)
        ]

        if not ready:
            logger.error(f"Dependency deadlock — remaining: {list(pending.keys())}")
            break

        # Run all ready tasks in parallel
        results = await asyncio.gather(
            *[_run_worker(t, query_engine, cwd) for t in ready],
            return_exceptions=False,
        )

        for task in results:
            del pending[task.id]
            if task.success:
                completed[task.id] = task
            else:
                failed.append(task)
                completed[task.id] = task  # mark done so deps unblock

    # Retry failures once
    if failed and max_retries > 0:
        logger.info(f"Retrying {len(failed)} failed tasks...")
        retry_results = await dispatch(failed, query_engine, cwd, max_retries=0)
        for t in retry_results:
            if t.success:
                completed[t.id] = t

    return list(completed.values())


# ---------------------------------------------------------------------------
# Assembly / verification
# ---------------------------------------------------------------------------

def verify_assembly(tasks: list[WorkerTask]) -> dict:
    """Check all create tasks produced their target files."""
    results = {"passed": [], "failed": [], "total": len(tasks)}
    for task in tasks:
        if task.verb == "create":
            exists = os.path.exists(task.target)
            if exists and task.success:
                results["passed"].append(task.target)
            else:
                results["failed"].append(task.target)
    return results


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

async def run_orchestrated(
    prompt: str,
    orchestrator_adapter,
    worker_query_engine,
    cwd: str = ".",
) -> dict:
    """Full orchestrator/worker pipeline for complex multi-file tasks.

    Returns dict with tasks, assembly results, and summary.
    """
    logger.info("Starting orchestrated execution...")

    # Step 1: Decompose
    tasks = await decompose(prompt, orchestrator_adapter, cwd)
    if not tasks:
        return {"error": "Decomposition failed — no tasks generated", "tasks": []}

    logger.info(f"Decomposed into {len(tasks)} tasks: {[t.target for t in tasks]}")

    # Step 2: Dispatch workers
    completed = await dispatch(tasks, worker_query_engine, cwd)

    # Step 3: Verify assembly
    assembly = verify_assembly(completed)

    summary = (
        f"Orchestrated {len(tasks)} tasks: "
        f"{len(assembly['passed'])} passed, {len(assembly['failed'])} failed"
    )
    logger.info(summary)

    return {
        "tasks": [
            {
                "id": t.id,
                "target": t.target,
                "success": t.success,
                "verb": t.verb,
            }
            for t in completed
        ],
        "assembly": assembly,
        "summary": summary,
    }
