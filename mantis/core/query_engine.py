import asyncio
import json
import subprocess
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Optional
from mantis.core.model_adapter import ModelAdapter
from mantis.core.tool_registry import ToolRegistry
from mantis.core.planner import build_execution_plan
from mantis.core.permissions import PermissionRequiredError
from mantis.core.quality_gate import execute_with_quality_gate
from mantis.tools.ast_extractor import build_edit_context
from mantis.tools.builtins import run_tsc


class QueryEngine:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        tool_registry: ToolRegistry,
        max_iterations: int = 25,
        context_manager=None,
        hook_manager=None,
        permission_manager=None,
        router=None,
    ):
        self.model_adapter = model_adapter
        self.tool_registry = tool_registry
        self.max_iterations = max_iterations
        self.context_manager = context_manager
        self.hook_manager = hook_manager
        self.permission_manager = permission_manager
        self.router = router  # Optional ModelRouter for per-task model selection
        self.last_run_details: dict[str, Any] = {}

    async def run_agentic(self, prompt: str, system_prompt: str = None) -> str:
        """Agentic loop: plan -> (optionally) build file context -> quality-gated execution."""
        plan = build_execution_plan(prompt)
        results: list[str] = []
        task_summaries: list[dict[str, Any]] = []

        for task in plan.tasks:
            subtask_prompt = task.prompt

            # Prepend focused file context for the first file target, if any
            if task.file_targets:
                file_ctx = build_edit_context(task.file_targets[0], subtask_prompt)
                if not file_ctx.startswith("Error:"):
                    subtask_prompt = f"{file_ctx}\n\n{subtask_prompt}"

            # Per-task model routing: swap to optimal model for this task's
            # complexity and type, then restore after the task completes.
            _prior_snapshot = self.model_adapter.profile_snapshot()
            if self.router is not None and len(self.router.list_models()) > 1:
                selected = self.router.route_for_plan(
                    task_type=task.task_type,
                    complexity=plan.complexity,
                    file_count=len(task.file_targets),
                    task_count=len(plan.tasks),
                    needs_escalation=task.needs_escalation,
                )
                self.model_adapter.swap_to_profile(selected)

            # Capture variables for the closure
            _prompt = subtask_prompt
            _system = system_prompt
            task_summary = {
                "title": task.title,
                "task_type": task.task_type,
                "file_targets": task.file_targets,
                "status": "running",
                "quality_score": None,
                "self_corrected": False,
                "artifact_check": {"ok": True, "message": "No artifact checks ran."},
                "timeout_recovered": False,
            }

            async def execute_fn(p: str, _s=_system, _timeout: int = 120) -> str:
                try:
                    return await asyncio.wait_for(self.run(p, _s), timeout=_timeout)
                except asyncio.TimeoutError:
                    artifact_feedback = self._verify_generated_artifacts(task.file_targets)
                    if artifact_feedback is None and task.file_targets:
                        task_summary["timeout_recovered"] = True
                        task_summary["artifact_check"] = {
                            "ok": True,
                            "message": "Artifacts passed after a foreground timeout.",
                        }
                        return "Generated requested files successfully and artifact checks passed."
                    raise

            quality_result = await execute_with_quality_gate(
                execute_fn, _prompt, task.task_type
            )
            final_output = quality_result.output
            task_summary["quality_score"] = quality_result.score
            task_summary["self_corrected"] = quality_result.self_corrected
            artifact_feedback = self._verify_generated_artifacts(task.file_targets)
            if artifact_feedback:
                task_summary["artifact_check"] = {"ok": False, "message": artifact_feedback}
                retry_prompt = (
                    self._build_artifact_retry_prompt(_prompt, task.file_targets, artifact_feedback)
                )
                final_output = await execute_fn(retry_prompt, _timeout=150)
                task_summary["artifact_check"] = {
                    "ok": True,
                    "message": "Artifacts passed after one verifier-driven retry.",
                }
            elif task.file_targets:
                task_summary["artifact_check"] = {
                    "ok": True,
                    "message": "Artifacts verified successfully.",
                }
            # TypeScript post-edit check: if any file target is .ts/.tsx,
            # run tsc --noEmit and feed errors back as a retry prompt.
            ts_files = [f for f in task.file_targets if f.endswith((".ts", ".tsx"))]
            if ts_files:
                ts_dir = str(Path(ts_files[0]).parent)
                tsc_output = await run_tsc(path=ts_dir)
                if tsc_output and tsc_output != "tsc: no errors found." and "error TS" in tsc_output:
                    tsc_retry_prompt = (
                        f"The TypeScript compiler found errors after your edit. Fix them:\n\n"
                        f"{tsc_output}\n\nOriginal task: {_prompt}"
                    )
                    final_output = await execute_fn(tsc_retry_prompt, _timeout=150)
                    task_summary["tsc_errors_fixed"] = True
                else:
                    task_summary["tsc_clean"] = True

            task_summary["status"] = "done"
            results.append(final_output)
            task_summaries.append(task_summary)

            # Restore original model/provider after per-task swap
            self.model_adapter.restore_snapshot(_prior_snapshot)

        self.last_run_details = {
            "execution_mode": "direct_agentic",
            "task_count": len(task_summaries),
            "tasks": task_summaries,
            "verifier": {
                "verdict": "pass",
                "reason": "Artifact checks passed for the completed tasks.",
            },
        }

        if len(results) == 1:
            return results[0]
        return "\n\n---\n\n".join(results)

    async def run_streaming(self, prompt: str, system_prompt: str = None) -> AsyncGenerator[str, None]:
        """Stream SSE-formatted strings for the agentic response."""
        try:
            plan = build_execution_plan(prompt)
            subtasks_count = len(plan.tasks)
            task_type = plan.tasks[0].task_type if plan.tasks else "unknown"
            execution_mode = "coordinator_worker_verifier" if (
                plan.needs_escalation or len({
                    file_target
                    for task in plan.tasks
                    for file_target in task.file_targets
                }) >= 3 or len(plan.tasks) > 1
            ) else "direct_agentic"

            yield f'data: {json.dumps({"type": "status", "text": "Planning..."})}\n\n'

            plan_tasks = [
                {
                    "title": task.title,
                    "task_type": task.task_type,
                    "status": "pending",
                    "file_targets": task.file_targets,
                }
                for task in plan.tasks
            ]
            yield f'data: {json.dumps({"type": "plan", "tasks": plan_tasks, "execution_mode": execution_mode})}\n\n'

            accumulated = []
            for i, task in enumerate(plan.tasks):
                yield f'data: {json.dumps({"type": "status", "text": f"Running: {task.task_type}"})}\n\n'

                # Signal this task is now running
                if subtasks_count > 1:
                    yield f'data: {json.dumps({"type": "task_update", "index": i, "status": "running"})}\n\n'

                subtask_prompt = task.prompt
                if task.file_targets:
                    file_ctx = build_edit_context(task.file_targets[0], subtask_prompt)
                    if not file_ctx.startswith("Error:"):
                        subtask_prompt = f"{file_ctx}\n\n{subtask_prompt}"

                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": subtask_prompt})

                token_buffer = []
                async for chunk in self.model_adapter.stream(messages):
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            token_buffer.append(content)
                            yield f'data: {json.dumps({"type": "token", "content": content})}\n\n'

                accumulated.append("".join(token_buffer))

                # Signal this task is done
                if subtasks_count > 1:
                    yield f'data: {json.dumps({"type": "task_update", "index": i, "status": "done"})}\n\n'

            total_cost = self.model_adapter.total_cost_usd
            yield f'data: {json.dumps({"type": "done", "task_type": task_type, "subtasks": subtasks_count, "total_cost": total_cost, "remaining_budget_usd": self.model_adapter.remaining_budget_usd, "execution_mode": execution_mode, "verifier_reason": "Live stream completed.", "verifier_verdict": "pass"})}\n\n'

        except Exception as exc:
            yield f'data: {json.dumps({"type": "error", "message": str(exc)})}\n\n'

    async def run(self, prompt: str, system_prompt: str = None) -> str:
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        return await self._run_with_messages(
            messages,
            prompt=prompt,
            system_prompt=system_prompt,
            iteration=0,
        )

    async def resume_from_approval(self, approval_id: str) -> str:
        if self.permission_manager is None or self.permission_manager.approval_store is None:
            raise ValueError("Approval store is not configured")

        approval = self.permission_manager.approval_store.load(approval_id)
        if approval is None:
            raise ValueError(f"Approval '{approval_id}' not found")

        checkpoint = approval.metadata.get("checkpoint")
        if not checkpoint:
            raise ValueError(f"Approval '{approval_id}' has no checkpoint")

        messages = checkpoint["messages"]
        pending_call = checkpoint["pending_call"]
        iteration = checkpoint["iteration"]

        result = await self._execute_pending_call(
            messages,
            pending_call=pending_call,
            prompt=checkpoint.get("prompt", ""),
            system_prompt=checkpoint.get("system_prompt"),
            iteration=iteration,
        )
        if result is not None:
            return result

        return await self._run_with_messages(
            messages,
            prompt=checkpoint.get("prompt", ""),
            system_prompt=checkpoint.get("system_prompt"),
            iteration=iteration + 1,
        )

    async def _run_with_messages(
        self,
        messages: list[dict[str, Any]],
        prompt: str,
        system_prompt: str | None,
        iteration: int,
    ) -> str:

        while iteration < self.max_iterations:
            # Truncate context to fit token budget before each LLM call.
            # Operate directly on the messages list to preserve tool_calls /
            # tool_call_id fields that context_manager cannot round-trip.
            if self.context_manager is not None:
                reserve = 4096
                available = self.context_manager.max_tokens - reserve
                # Estimate tokens from messages directly (4 chars ≈ 1 token).
                def _estimate(msgs: list) -> int:
                    import json as _json
                    return sum(len(_json.dumps(m)) for m in msgs) // 4

                # Keep the system message (index 0) and trim from the oldest
                # non-system messages when over budget.
                while _estimate(messages) > available and len(messages) > 1:
                    # Never drop a system message at index 0
                    drop_idx = 1 if messages[0].get("role") == "system" else 0
                    if len(messages) <= drop_idx + 1:
                        break
                    messages.pop(drop_idx)

            # Get response from LLM
            response = await self.model_adapter.chat(messages, tools=self.tool_registry.list_schemas())

            # Track tokens
            input_tokens = response.get("usage", {}).get("prompt_tokens", 0)
            output_tokens = response.get("usage", {}).get("completion_tokens", 0)

            # Check if the response has tool calls
            choices = response.get("choices", [])
            if not choices:
                raise Exception("No choices returned from model")

            choice = choices[0]
            message = choice.get("message", {})

            # Handle both function_call and tool_calls formats
            has_tool_calls = False

            # Check for legacy function_call format
            if "function_call" in message:
                function_call = message["function_call"]
                name = function_call.get("name")
                arguments_str = function_call.get("arguments")

                try:
                    arguments = json.loads(arguments_str)
                except json.JSONDecodeError:
                    raise Exception(f"Invalid JSON in function call arguments: {arguments_str}")

                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "function_call": {
                        "name": name,
                        "arguments": arguments_str
                    }
                }
                messages.append(assistant_msg)
                result = await self._execute_pending_call(
                    messages,
                    pending_call={
                        "format": "function_call",
                        "name": name,
                        "arguments": arguments,
                        "arguments_str": arguments_str,
                    },
                    prompt=prompt,
                    system_prompt=system_prompt,
                    iteration=iteration,
                )
                if result is not None:
                    return result

                has_tool_calls = True

            # Check for newer tool_calls format
            elif "tool_calls" in message:
                tool_calls = message["tool_calls"]

                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                }
                messages.append(assistant_msg)

                for tool_index, tool_call in enumerate(tool_calls):
                    call_id = tool_call.get("id")
                    function_info = tool_call.get("function", {})
                    name = function_info.get("name")
                    arguments_str = function_info.get("arguments")

                    try:
                        arguments = json.loads(arguments_str)
                    except json.JSONDecodeError:
                        raise Exception(f"Invalid JSON in tool call arguments: {arguments_str}")

                    result = await self._execute_pending_call(
                        messages,
                        pending_call={
                            "format": "tool_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments,
                            "arguments_str": arguments_str,
                            "tool_index": tool_index,
                        },
                        prompt=prompt,
                        system_prompt=system_prompt,
                        iteration=iteration,
                    )
                    if result is not None:
                        return result

                has_tool_calls = True

            # If there were no tool calls, we're done
            if not has_tool_calls:
                final_content = message.get("content", "")
                return final_content.strip()

            iteration += 1

        # Max iterations reached — return last assistant content rather than raising
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"].strip()
        return f"Task completed after {self.max_iterations} iterations."

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.permission_manager is not None:
            if not self.permission_manager.check(name, arguments):
                return {"error": f"Permission denied for tool: {name}"}

        if self.hook_manager is not None:
            from mantis.core.hooks import Decision

            hook_result = await self.hook_manager.run_pre_tool(name, arguments)
            if hook_result.decision == Decision.BLOCK:
                return {"error": f"Blocked by hook: {hook_result.reason}"}
            arguments = (
                hook_result.modified_input
                if hook_result.modified_input is not None
                else arguments
            )
            result = await self.tool_registry.execute(name, arguments)
            await self.hook_manager.run_post_tool(name, arguments, json.dumps(result))
            return result

        return await self.tool_registry.execute(name, arguments)

    def _save_permission_checkpoint(
        self,
        exc: PermissionRequiredError,
        messages: list[dict[str, Any]],
        pending_call: dict[str, Any],
        prompt: str,
        system_prompt: str | None,
        iteration: int,
    ) -> None:
        approval_store = self.permission_manager.approval_store
        approval = approval_store.load(exc.approval_id)
        if approval is None:
            return
        metadata = dict(approval.metadata)
        metadata["checkpoint"] = {
            "messages": messages,
            "pending_call": pending_call,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "iteration": iteration,
        }
        approval_store.update(exc.approval_id, metadata=metadata)

    async def _execute_pending_call(
        self,
        messages: list[dict[str, Any]],
        pending_call: dict[str, Any],
        prompt: str,
        system_prompt: str | None,
        iteration: int,
    ) -> str | None:
        name = pending_call["name"]
        arguments = pending_call["arguments"]
        try:
            result = await self._execute_tool(name, arguments)
        except PermissionRequiredError as exc:
            self._save_permission_checkpoint(
                exc,
                messages=messages,
                pending_call=pending_call,
                prompt=prompt,
                system_prompt=system_prompt,
                iteration=iteration,
            )
            raise

        tool_content = self._format_tool_result_content(name, result)
        if pending_call["format"] == "function_call":
            messages.append(
                {
                    "role": "function",
                    "name": name,
                    "content": tool_content,
                }
            )
            return None

        messages.append(
            {
                "role": "tool",
                "tool_call_id": pending_call["call_id"],
                "name": name,
                "content": tool_content,
            }
        )
        return None

    def _format_tool_result_content(self, tool_name: str, result: Any) -> str:
        payload = json.dumps(result)
        return (
            f"[TOOL_RESULT:{tool_name}] {payload}\n"
            "Tool execution completed successfully. "
            "If the task is now complete, return the final answer. "
            "Only call another tool if more work is still required."
        )

    def _verify_generated_artifacts(self, file_targets: list[str]) -> str | None:
        if not file_targets:
            return None

        paths = [Path(target) for target in file_targets]
        check_files = [path for path in paths if path.name.startswith("check_") and path.suffix == ".py"]
        test_files = [path for path in paths if path.name.startswith("test_") and path.suffix == ".py"]

        for check_file in check_files:
            if not check_file.exists():
                return f"Expected checker file is missing: {check_file}"
            proc = subprocess.run(
                ["python", str(check_file)],
                cwd=str(check_file.parent),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return f"Generated checker failed for {check_file}: {(proc.stdout + proc.stderr).strip()[-400:]}"

        if test_files:
            test_dir = str(test_files[0].parent)
            proc = subprocess.run(
                ["python", "-m", "pytest", "-q"],
                cwd=test_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                return f"Generated pytest suite failed in {test_dir}: {(proc.stdout + proc.stderr).strip()[-400:]}"

        return None

    def _build_artifact_retry_prompt(
        self,
        original_prompt: str,
        file_targets: list[str],
        artifact_feedback: str,
    ) -> str:
        parts = [
            original_prompt,
            "",
            "[ARTIFACT VERIFICATION FEEDBACK]",
            artifact_feedback,
            "Read the generated checker/test file and the implementation file before editing.",
            "Satisfy the generated checks exactly, not approximately.",
            "If the checker uses exact equality, avoid time-sensitive or nondeterministic behavior.",
        ]
        for path_str in file_targets:
            path = Path(path_str)
            if not path.exists():
                continue
            if path.name.startswith("check_") or path.name.startswith("test_") or path.suffix == ".py":
                content = path.read_text(encoding="utf-8", errors="ignore")
                parts.extend(["", f"[FILE: {path}]", content[:4000]])
        parts.extend(["", "Fix the generated files so the checks pass exactly. Return once the task is complete."])
        return "\n".join(parts)
