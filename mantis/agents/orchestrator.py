from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mantis.agents.spawner import AgentSpawner
from mantis.core.planner import ExecutionPlan
from mantis.core.system_prompt import build_role_prompt


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
    revised: bool = False


class CoordinatorOrchestrator:
    def __init__(
        self,
        model_adapter,
        tool_registry,
        project_instructions: str | None = None,
        worker_model_adapter=None,
    ):
        self.model_adapter = model_adapter
        self.tool_registry = tool_registry
        self.project_instructions = project_instructions
        self.spawner = AgentSpawner(
            model_adapter=model_adapter,
            tool_registry=tool_registry,
            worker_model_adapter=worker_model_adapter,
        )

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
            revised=revised,
        )

    async def _run_workers(self, plan: ExecutionPlan) -> list[str]:
        worker_prompt = build_role_prompt(
            "worker",
            project_instructions=self.project_instructions,
            cost_aware=True,
        )
        tasks = [task.prompt for task in plan.tasks]
        if plan.can_run_in_parallel and len(tasks) > 1:
            results = await self.spawner.spawn_parallel(tasks, system_prompt=worker_prompt)
        else:
            results = [
                await self.spawner.spawn(task_prompt, system_prompt=worker_prompt)
                for task_prompt in tasks
            ]
        return [result.output for result in results]

    async def _run_single_worker(self, prompt: str) -> str:
        worker_prompt = build_role_prompt(
            "worker",
            project_instructions=self.project_instructions,
            cost_aware=True,
        )
        result = await self.spawner.spawn(prompt, system_prompt=worker_prompt)
        return result.output

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
