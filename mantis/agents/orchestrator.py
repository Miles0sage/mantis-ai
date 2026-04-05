from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mantis.agents.spawner import AgentSpawner
from mantis.core.planner import ExecutionPlan
from mantis.core.system_prompt import build_role_prompt
from mantis.core.worktree_manager import (
    collect_git_review,
    create_issue_worktree,
    is_git_repo,
    rewrite_prompt_paths_for_worktree,
)


@dataclass
class VerificationResult:
    verdict: str
    reason: str
    missing: list[str]


@dataclass
class OrchestrationResult:
    output: str
    worker_outputs: list[str]
    verification: VerificationResult
    workers: list[dict[str, Any]]
    revised: bool = False


class CoordinatorOrchestrator:
    def __init__(
        self,
        model_adapter,
        tool_registry,
        project_instructions: str | None = None,
        worker_model_adapter=None,
        project_dir: str | None = None,
        isolate_workers: bool = True,
        worker_root_dir: str | None = None,
        repeated_tool_call_limit: int = 2,
    ):
        self.model_adapter = model_adapter
        self.tool_registry = tool_registry
        self.project_instructions = project_instructions
        self.project_dir = os.path.abspath(project_dir) if project_dir else None
        self.isolate_workers = isolate_workers
        self.worker_root_dir = worker_root_dir
        self.repeated_tool_call_limit = repeated_tool_call_limit
        self.spawner = AgentSpawner(
            model_adapter=model_adapter,
            tool_registry=tool_registry,
            worker_model_adapter=worker_model_adapter,
        )
        self._last_worker_results: list[Any] = []

    async def execute(self, prompt: str, plan: ExecutionPlan) -> OrchestrationResult:
        worker_outputs = await self._run_workers(plan)
        combined = self._combine_outputs(worker_outputs)
        verification = await self._verify(prompt, combined, plan)
        revised = False

        if verification.verdict != "pass":
            revised = True
            revision_prompt = (
                f"{prompt}\n\nVerifier feedback:\n{verification.reason}\n"
                f"Missing requirements: {', '.join(verification.missing) or 'none'}\n"
                "Revise the implementation to satisfy the request exactly."
            )
            worker_outputs = [await self._run_single_worker(revision_prompt)]
            combined = self._combine_outputs(worker_outputs)
            verification = await self._verify(prompt, combined, plan)

        return OrchestrationResult(
            output=combined,
            worker_outputs=worker_outputs,
            verification=verification,
            workers=self._summarize_workers(self._last_worker_results),
            revised=revised,
        )

    async def _run_workers(self, plan: ExecutionPlan) -> list[str]:
        worker_prompt = build_role_prompt(
            "worker",
            project_instructions=self.project_instructions,
            cost_aware=True,
        )
        prepared = [self._prepare_worker_task(task, index) for index, task in enumerate(plan.tasks, start=1)]
        can_parallelize = plan.can_run_in_parallel and len(prepared) > 1 and not self._has_overlapping_targets(prepared)
        if can_parallelize:
            results = await self.spawner.spawn_parallel(
                prepared,
                system_prompt=worker_prompt,
                repeated_tool_call_limit=self.repeated_tool_call_limit,
            )
        else:
            results = [
                await self.spawner.spawn(
                    task_spec["prompt"],
                    system_prompt=worker_prompt,
                    default_bash_cwd=task_spec.get("default_bash_cwd"),
                    metadata=task_spec.get("metadata"),
                    repeated_tool_call_limit=self.repeated_tool_call_limit,
                )
                for task_spec in prepared
            ]
        for result in results:
            self._enrich_worker_result(result)
        self._last_worker_results = results
        return [result.output for result in results]

    async def _run_single_worker(self, prompt: str) -> str:
        worker_prompt = build_role_prompt(
            "worker",
            project_instructions=self.project_instructions,
            cost_aware=True,
        )
        result = await self.spawner.spawn(
            prompt,
            system_prompt=worker_prompt,
            repeated_tool_call_limit=self.repeated_tool_call_limit,
        )
        self._enrich_worker_result(result)
        self._last_worker_results = [result]
        return result.output

    def _prepare_worker_task(self, task: Any, index: int) -> dict[str, Any]:
        prompt = task.prompt
        original_prompt = task.prompt
        file_targets = list(task.file_targets)
        worktree = None
        project_dir = self.project_dir

        if (
            self.isolate_workers
            and self.project_dir
            and is_git_repo(self.project_dir)
            and task.file_targets
        ):
            title = f"worker-{index}-{task.title}"
            try:
                worktree = create_issue_worktree(
                    repo_dir=self.project_dir,
                    title=title,
                    root_dir=self.worker_root_dir,
                )
                prompt, file_targets = rewrite_prompt_paths_for_worktree(
                    prompt,
                    repo_dir=self.project_dir,
                    worktree_dir=worktree["worktree_dir"],
                    file_targets=file_targets,
                )
                project_dir = worktree["worktree_dir"]
            except RuntimeError:
                worktree = None

        prompt = self._augment_worker_prompt(
            prompt,
            file_targets=file_targets,
            project_dir=project_dir,
            worktree=worktree,
        )
        metadata = {
            "task_index": index,
            "title": task.title,
            "prompt": prompt,
            "task_type": task.task_type,
            "dependencies": list(task.dependencies),
            "parallel_group": task.parallel_group,
            "file_targets": file_targets,
            "project_dir": project_dir,
            "worktree": worktree,
            "resume_metadata": self._build_resume_metadata(
                index=index,
                title=task.title,
                prompt=prompt,
                original_prompt=original_prompt,
                file_targets=file_targets,
                dependencies=list(task.dependencies),
                project_dir=project_dir,
                worktree=worktree,
            ),
        }
        return {
            "prompt": prompt,
            "default_bash_cwd": project_dir,
            "metadata": metadata,
        }

    def _augment_worker_prompt(
        self,
        prompt: str,
        *,
        file_targets: list[str],
        project_dir: str | None,
        worktree: dict[str, str] | None,
    ) -> str:
        instructions = []
        if worktree:
            instructions.extend(
                [
                    "[WORKER ISOLATION]",
                    f"Operate inside isolated worktree: {worktree['worktree_dir']}",
                    f"Branch: {worktree['branch']}",
                    "If you use run_bash, pass cwd with the worktree path or rely on the default worker cwd.",
                    "Do not modify files outside the isolated worktree unless the task explicitly requires it.",
                ]
            )
        elif project_dir:
            instructions.extend(
                [
                    "[WORKER CONTEXT]",
                    f"Project directory: {project_dir}",
                    "Use absolute file paths when possible.",
                ]
            )
        if file_targets:
            instructions.append("Owned targets: " + ", ".join(file_targets))
        if not instructions:
            return prompt
        return "\n".join(instructions) + "\n\n" + prompt

    def _summarize_workers(self, results: list[Any]) -> list[dict[str, Any]]:
        workers: list[dict[str, Any]] = []
        for result in results:
            metadata = result.metadata or {}
            workers.append(
                {
                    "agent_id": result.agent_id,
                    "status": result.status,
                    "duration_ms": round(result.duration_ms, 2),
                    "task_index": metadata.get("task_index"),
                    "title": metadata.get("title"),
                    "task_type": metadata.get("task_type"),
                    "dependencies": metadata.get("dependencies") or [],
                    "parallel_group": metadata.get("parallel_group"),
                    "project_dir": metadata.get("project_dir"),
                    "file_targets": metadata.get("file_targets") or [],
                    "worktree": metadata.get("worktree"),
                    "changed_files": metadata.get("changed_files") or [],
                    "diff_preview": metadata.get("diff_preview"),
                    "resume_metadata": metadata.get("resume_metadata"),
                    "token_usage": result.token_usage,
                }
            )
        return workers

    def _build_resume_metadata(
        self,
        *,
        index: int,
        title: str,
        prompt: str,
        original_prompt: str,
        file_targets: list[str],
        dependencies: list[str],
        project_dir: str | None,
        worktree: dict[str, str] | None,
    ) -> dict[str, Any]:
        return {
            "resume_key": f"worker-{index}",
            "task_index": index,
            "title": title,
            "prompt": original_prompt,
            "execution_prompt": prompt,
            "file_targets": file_targets,
            "dependencies": dependencies,
            "project_dir": project_dir,
            "worktree_branch": (worktree or {}).get("branch"),
            "worktree_dir": (worktree or {}).get("worktree_dir"),
            "resumable": True,
        }

    def _has_overlapping_targets(self, prepared: list[dict[str, Any]]) -> bool:
        seen: set[str] = set()
        for task_spec in prepared:
            targets = {
                str(Path(target).resolve())
                for target in task_spec.get("metadata", {}).get("file_targets", [])
            }
            if seen.intersection(targets):
                return True
            seen.update(targets)
        return False

    def _enrich_worker_result(self, result: Any) -> None:
        metadata = result.metadata or {}
        project_dir = metadata.get("project_dir")
        worktree = metadata.get("worktree")
        if not project_dir or not worktree:
            return
        try:
            git_review = collect_git_review(project_dir)
        except RuntimeError:
            return
        metadata["changed_files"] = git_review.get("changed_files") or []
        metadata["diff_preview"] = git_review.get("diff")
        metadata["worktree"] = {
            **worktree,
            "path": git_review.get("path") or worktree.get("worktree_dir"),
            "branch": git_review.get("branch") or worktree.get("branch"),
        }
        result.metadata = metadata

    def _combine_outputs(self, outputs: list[str]) -> str:
        if len(outputs) == 1:
            return outputs[0]
        return "\n\n---\n\n".join(outputs)

    async def _verify(self, prompt: str, output: str, plan: ExecutionPlan) -> VerificationResult:
        artifact_missing, artifact_summary = self._artifact_verify(prompt, plan)
        verifier_prompt = build_role_prompt(
            "verifier",
            project_instructions=self.project_instructions,
            cost_aware=True,
        )
        user_prompt = (
            "Check whether the implementation output satisfies the original request.\n"
            "Return strict JSON with keys verdict, reason, missing.\n"
            "Use verdict pass or fail only.\n\n"
            f"Original request:\n{prompt}\n\n"
            f"Artifact summary:\n{artifact_summary}\n\n"
            f"Implementation output:\n{output}\n\n"
            "Remember: Return ONLY JSON, no other text."
        )
        response = await self.model_adapter.chat(
            [
                {"role": "system", "content": verifier_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=None,
            temperature=0.0,
        )
        content = response["choices"][0]["message"].get("content", "").strip()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = {
                "verdict": "fail",
                "reason": f"Verifier returned non-JSON output: {content[:200]}",
                "missing": ["structured verification output"],
            }
        verdict = str(payload.get("verdict", "fail")).lower()
        if verdict not in {"pass", "fail"}:
            verdict = "fail"
        missing = payload.get("missing", [])
        if not isinstance(missing, list):
            missing = [str(missing)]
        for item in artifact_missing:
            if item not in missing:
                missing.append(item)
        if artifact_missing:
            verdict = "fail"
            reason = str(payload.get("reason", ""))
            payload["reason"] = (reason + " Artifact verification failed.").strip()
        return VerificationResult(
            verdict=verdict,
            reason=str(payload.get("reason", "")),
            missing=[str(item) for item in missing],
        )

    def _artifact_verify(self, prompt: str, plan: ExecutionPlan) -> tuple[list[str], str]:
        file_targets = []
        for task in plan.tasks:
            for target in task.file_targets:
                if target not in file_targets:
                    file_targets.append(target)

        missing: list[str] = []
        summary_lines: list[str] = []
        required_classes = self._extract_required_classes(prompt)
        required_functions = self._extract_required_functions(prompt)
        required_methods = self._extract_required_methods(prompt)

        primary_python_target = next((target for target in file_targets if Path(target).suffix == ".py"), None)
        python_files: dict[str, str] = {}

        for target in file_targets:
            path = Path(target)
            if not path.exists():
                missing.append(f"missing file: {target}")
                summary_lines.append(f"{target}: MISSING")
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            if path.suffix == ".py":
                python_files[target] = content
            summary_lines.append(f"{target}: present, {len(content)} chars")
            if target == primary_python_target:
                for class_name in required_classes:
                    if class_name not in content:
                        missing.append(f"missing class {class_name} in {target}")
                for func_name in required_functions:
                    if f"def {func_name}(" not in content and f"class {func_name}" not in content:
                        missing.append(f"missing function {func_name} in {target}")
                for method_name in required_methods:
                    if f"def {method_name}(" not in content:
                        missing.append(f"missing method {method_name} in {target}")
        missing.extend(self._detect_nondeterministic_exactness(prompt, primary_python_target, python_files))
        missing.extend(self._run_artifact_checks(file_targets))
        if not file_targets:
            summary_lines.append("No concrete file targets detected.")
        return missing, "\n".join(summary_lines)

    def _detect_nondeterministic_exactness(
        self,
        prompt: str,
        primary_python_target: str | None,
        python_files: dict[str, str],
    ) -> list[str]:
        if primary_python_target is None:
            return []
        if "do not run the check" not in prompt.lower() and "do not run pytest" not in prompt.lower():
            return []

        primary_content = python_files.get(primary_python_target, "")
        if not primary_content:
            return []

        uses_wall_clock = any(
            marker in primary_content
            for marker in ("time.time(", "datetime.now(", "time.monotonic(", "sleep(")
        )
        if not uses_wall_clock:
            return []

        issues: list[str] = []
        for path, content in python_files.items():
            if path == primary_python_target:
                continue
            if "assert" not in content:
                continue
            exact_numeric_assert = re.search(r"assert .+ == [-+]?\d+(?:\.\d+)?", content)
            if exact_numeric_assert:
                issues.append(
                    f"likely nondeterministic exactness mismatch: {primary_python_target} uses wall-clock state while {path} asserts exact numeric values"
                )
                break
        return issues

    def _run_artifact_checks(self, file_targets: list[str]) -> list[str]:
        issues: list[str] = []
        paths = [Path(target) for target in file_targets]
        check_files = [path for path in paths if path.name.startswith("check_") and path.suffix == ".py"]
        test_files = [path for path in paths if path.name.startswith("test_") and path.suffix == ".py"]

        for check_file in check_files:
            if not check_file.exists():
                continue
            proc = subprocess.run(
                ["python", str(check_file)],
                cwd=str(check_file.parent),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr).strip()[-300:]
                issues.append(f"generated checker failed: {check_file} :: {tail}")

        if test_files:
            test_dir = test_files[0].parent
            proc = subprocess.run(
                ["python", "-m", "pytest", "-q"],
                cwd=str(test_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr).strip()[-300:]
                issues.append(f"generated pytest suite failed in {test_dir} :: {tail}")

        return issues

    def _extract_required_classes(self, prompt: str) -> list[str]:
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

    def _extract_required_functions(self, prompt: str) -> list[str]:
        seen: list[str] = []
        for match in re.findall(r"\b([a-z_][A-Za-z0-9_]*) function\b", prompt):
            if match not in seen:
                seen.append(match)
        for match in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", prompt):
            if match not in seen and match not in {"__init__", "allow", "available"}:
                seen.append(match)
        return seen

    def _extract_required_methods(self, prompt: str) -> list[str]:
        seen: list[str] = []
        method_block = re.search(r"methods? ([^.]+)", prompt, flags=re.IGNORECASE)
        if method_block:
            for match in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|:|,|$)", method_block.group(1)):
                if match not in seen:
                    seen.append(match)
        return seen
