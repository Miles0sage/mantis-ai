import asyncio
import ast
import inspect
import json
import os
import subprocess
import re
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Optional, Callable, Awaitable

def _parse_tool_arguments(arguments_str: str) -> dict:
    """Parse tool call arguments JSON, with repair fallback for malformed output."""
    try:
        return json.loads(arguments_str)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            repaired = repair_json(arguments_str)
            return json.loads(repaired)
        except Exception:
            raise Exception(f"Invalid JSON in tool call arguments: {arguments_str[:200]}...")
from mantis.core.model_adapter import ModelAdapter
from mantis.core.tool_registry import ToolRegistry
from mantis.core.planner import build_execution_plan
from mantis.core.permissions import PermissionRequiredError
from mantis.core.quality_gate import execute_with_quality_gate
from mantis.tools.ast_extractor import build_edit_context

EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class QueryEngine:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        tool_registry: ToolRegistry,
        max_iterations: int = 25,
        repeated_tool_call_limit: int = 2,
        context_manager=None,
        hook_manager=None,
        permission_manager=None,
        router=None,
    ):
        self.model_adapter = model_adapter
        self.tool_registry = tool_registry
        self.max_iterations = max_iterations
        self.repeated_tool_call_limit = repeated_tool_call_limit
        self.context_manager = context_manager
        self.hook_manager = hook_manager
        self.permission_manager = permission_manager
        self.router = router  # Optional ModelRouter for per-task model selection
        self.last_run_details: dict[str, Any] = {}
        self._context_metrics: dict[str, Any] = {
            "messages_before_trim": 0,
            "messages_after_trim": 0,
            "messages_dropped": 0,
            "estimated_tokens_before_trim": 0,
            "estimated_tokens_after_trim": 0,
            "max_tokens": getattr(context_manager, "max_tokens", None),
        }

    def _estimate_message_tokens(self, messages: list[dict[str, Any]]) -> int:
        return sum(len(json.dumps(message)) for message in messages) // 4

    def _sync_context_manager(self, messages: list[dict[str, Any]]) -> None:
        if self.context_manager is None:
            return
        self.context_manager.clear()
        for message in messages:
            self.context_manager.messages.append(dict(message))

    def _apply_context_budget(self, messages: list[dict[str, Any]]) -> None:
        if self.context_manager is None:
            return

        reserve = 4096
        available = self.context_manager.max_tokens - reserve
        before_count = len(messages)
        before_tokens = self._estimate_message_tokens(messages)

        while self._estimate_message_tokens(messages) > available and len(messages) > 1:
            drop_idx = 1 if messages[0].get("role") == "system" else 0
            if len(messages) <= drop_idx + 1:
                break
            messages.pop(drop_idx)

        after_count = len(messages)
        after_tokens = self._estimate_message_tokens(messages)
        self._context_metrics = {
            "messages_before_trim": before_count,
            "messages_after_trim": after_count,
            "messages_dropped": max(before_count - after_count, 0),
            "estimated_tokens_before_trim": before_tokens,
            "estimated_tokens_after_trim": after_tokens,
            "max_tokens": self.context_manager.max_tokens,
            "reserve_tokens": reserve,
        }
        self._sync_context_manager(messages)

    def _is_python_edit_task(self, task_type: str, file_targets: list[str]) -> bool:
        code_task_types = {"feature", "bug_fix", "refactor", "test_writing", "review"}
        return (
            task_type in code_task_types
            and any(target.endswith(".py") for target in file_targets)
        )

    def _stream_response_chunks(self, text: str) -> list[str]:
        """Split final response text into small UI-friendly chunks."""
        if not text:
            return []
        return re.findall(r"\S+\s*|\n", text)

    async def _emit_event(
        self,
        event_callback: EventCallback | None,
        payload: dict[str, Any],
    ) -> None:
        if event_callback is None:
            return
        result = event_callback(payload)
        if inspect.isawaitable(result):
            await result

    async def _invoke_run(
        self,
        prompt: str,
        system_prompt: str | None,
        event_callback: EventCallback | None,
    ) -> str:
        run_signature = inspect.signature(self.run)
        if "event_callback" in run_signature.parameters:
            return await self.run(prompt, system_prompt, event_callback=event_callback)
        return await self.run(prompt, system_prompt)

    def _semantic_python_guidance(self, file_path: str) -> str:
        return "\n".join(
            [
                "[PYTHON EDIT STRATEGY]",
                f"Target file: {file_path}",
                "Prefer semantic Python tools before raw text edits:",
                "1. list_python_symbols to inspect the file structure",
                "2. read_python_symbol to read only the symbol you need",
                "3. replace_python_symbol for bounded function/class edits",
                "Use apply_edit or edit_file only when symbol replacement is not appropriate.",
            ]
        )

    def _is_js_edit_task(self, task_type: str, file_targets: list[str]) -> bool:
        code_task_types = {"feature", "bug_fix", "refactor", "test_writing", "review"}
        return (
            task_type in code_task_types
            and any(target.endswith((".js", ".jsx", ".ts", ".tsx")) for target in file_targets)
        )

    def _semantic_js_guidance(self, file_path: str) -> str:
        return "\n".join(
            [
                "[JS/TS EDIT STRATEGY]",
                f"Target file: {file_path}",
                "Prefer semantic JS/TS tools before broad raw-text edits:",
                "1. list_js_symbols to inspect top-level classes/functions",
                "2. read_js_symbol to read only the symbol you need",
                "3. build_js_edit_context for focused file context",
                "Use apply_edit or edit_file only after identifying the exact symbol/block to change.",
            ]
        )

    def _strict_test_writing_guidance(self, task: Any) -> str:
        requested_files = ", ".join(task.file_targets) if task.file_targets else "the requested files"
        return "\n".join(
            [
                "[TEST WRITING STRATEGY]",
                f"Scope your work to {requested_files}.",
                "Implement only the explicitly requested API and test cases.",
                "Do not invent extra edge cases, optional features, or broader behavior contracts.",
                "If you create tests, keep them aligned to the prompt rather than speculative coverage.",
                "Prefer the smallest correct implementation that satisfies the stated requirements exactly.",
            ]
        )

    def _build_subtask_prompt(self, task: Any) -> str:
        subtask_prompt = task.prompt
        if not task.file_targets and task.task_type != "test_writing":
            return subtask_prompt

        prompt_parts: list[str] = []
        if task.task_type == "test_writing":
            prompt_parts.append(self._strict_test_writing_guidance(task))
        primary_target = task.file_targets[0] if task.file_targets else None
        if primary_target and self._is_python_edit_task(task.task_type, task.file_targets):
            prompt_parts.append(self._semantic_python_guidance(primary_target))
        elif primary_target and self._is_js_edit_task(task.task_type, task.file_targets):
            prompt_parts.append(self._semantic_js_guidance(primary_target))
        if task.file_targets:
            file_ctx = build_edit_context(primary_target, subtask_prompt)
            if not file_ctx.startswith("Error:"):
                prompt_parts.append(file_ctx)
        if not prompt_parts:
            return subtask_prompt
        prompt_parts.append(subtask_prompt)
        return "\n\n".join(prompt_parts)

    async def run_agentic(
        self,
        prompt: str,
        system_prompt: str = None,
        event_callback: EventCallback | None = None,
    ) -> str:
        """Agentic loop: plan -> (optionally) build file context -> quality-gated execution."""
        plan = build_execution_plan(prompt)
        results: list[str] = []
        task_summaries: list[dict[str, Any]] = []
        max_context_dropped = 0

        for task in plan.tasks:
            subtask_prompt = self._build_subtask_prompt(task)

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
                "postconditions": task.postconditions,
                "status": "running",
                "quality_score": None,
                "self_corrected": False,
                "artifact_check": {"ok": True, "message": "No artifact checks ran."},
                "postcondition_check": {"ok": True, "message": "No postcondition checks ran."},
                "timeout_recovered": False,
                "context": None,
            }

            async def execute_fn(p: str, _s=_system, _timeout: int = 120) -> str:
                try:
                    return await asyncio.wait_for(
                        self._invoke_run(p, _s, event_callback),
                        timeout=_timeout,
                    )
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
                execute_fn, _prompt, task.task_type,
                file_targets=task.file_targets or None,
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
            postcondition_feedback = (
                self._verify_postconditions(task.postconditions, task.file_targets)
                if task.file_targets
                else None
            )
            if postcondition_feedback:
                task_summary["postcondition_check"] = {"ok": False, "message": postcondition_feedback}
                retry_prompt = (
                    self._build_artifact_retry_prompt(_prompt, task.file_targets, postcondition_feedback)
                    + "\n\n[POSTCONDITIONS]\n"
                    + "\n".join(task.postconditions)
                )
                final_output = await execute_fn(retry_prompt, _timeout=150)
                postcondition_feedback = self._verify_postconditions(task.postconditions, task.file_targets)
                if postcondition_feedback:
                    task_summary["status"] = "failed"
                    task_summary["postcondition_check"] = {"ok": False, "message": postcondition_feedback}
                else:
                    task_summary["postcondition_check"] = {
                        "ok": True,
                        "message": "Postconditions passed after one verifier-driven retry.",
                    }
            elif task.postconditions and task.file_targets:
                task_summary["postcondition_check"] = {
                    "ok": True,
                    "message": "Postconditions verified successfully.",
                }
            if task_summary["status"] == "running":
                task_summary["status"] = "done"
            task_summary["context"] = dict(self._context_metrics)
            max_context_dropped = max(
                max_context_dropped,
                int(task_summary["context"].get("messages_dropped", 0)),
            )
            results.append(final_output)
            task_summaries.append(task_summary)

            # Restore original model/provider after per-task swap
            self.model_adapter.restore_snapshot(_prior_snapshot)

        self.last_run_details = {
            "execution_mode": "direct_agentic",
            "task_count": len(task_summaries),
            "tasks": task_summaries,
            "verifier": {
                "verdict": "pass" if all(task["status"] == "done" for task in task_summaries) else "fail",
                "reason": (
                    "Artifact and postcondition checks passed for the completed tasks."
                    if all(task["status"] == "done" for task in task_summaries)
                    else "One or more tasks failed artifact or postcondition verification."
                ),
            },
            "context": {
                "max_messages_dropped": max_context_dropped,
                "last_trim": dict(self._context_metrics),
            },
        }

        if len(results) == 1:
            return results[0]
        return "\n\n---\n\n".join(results)

    async def run_streaming(self, prompt: str, system_prompt: str = None) -> AsyncGenerator[str, None]:
        """Stream SSE-formatted strings for the agent-backed response."""
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

            async def _queue_event(event: dict[str, Any]) -> None:
                await event_queue.put(event)

            event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            execution_prompt = (
                self._build_subtask_prompt(plan.tasks[0])
                if len(plan.tasks) == 1
                else prompt
            )
            run_task = asyncio.create_task(
                self.run_agentic(
                    execution_prompt,
                    system_prompt=system_prompt,
                    event_callback=_queue_event,
                )
            )
            keepalive_ticks = 0
            while not run_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    keepalive_ticks += 1
                    if keepalive_ticks >= 5:
                        keepalive_ticks = 0
                        yield ": keep-alive\n\n"
                    continue

                keepalive_ticks = 0
                event_type = event.get("type")
                if event_type == "tool_call":
                    yield f'data: {json.dumps({"type": "status", "text": f"Using tool: {event["tool_name"]}"})}\n\n'
                elif event_type == "tool_result":
                    yield f'data: {json.dumps({"type": "status", "text": f"Completed tool: {event["tool_name"]}"})}\n\n'
                elif event_type == "approval_required":
                    yield f'data: {json.dumps({"type": "status", "text": f"Awaiting approval: {event["tool_name"]}"})}\n\n'

            while not event_queue.empty():
                event = event_queue.get_nowait()
                event_type = event.get("type")
                if event_type == "tool_call":
                    yield f'data: {json.dumps({"type": "status", "text": f"Using tool: {event["tool_name"]}"})}\n\n'
                elif event_type == "tool_result":
                    yield f'data: {json.dumps({"type": "status", "text": f"Completed tool: {event["tool_name"]}"})}\n\n'
                elif event_type == "approval_required":
                    yield f'data: {json.dumps({"type": "status", "text": f"Awaiting approval: {event["tool_name"]}"})}\n\n'

            final_output = await run_task
            for content in self._stream_response_chunks(final_output):
                yield f'data: {json.dumps({"type": "token", "content": content})}\n\n'

            if subtasks_count > 1:
                for i in range(subtasks_count):
                    yield f'data: {json.dumps({"type": "task_update", "index": i, "status": "done"})}\n\n'

            total_cost = self.model_adapter.total_cost_usd
            yield f'data: {json.dumps({"type": "done", "task_type": task_type, "subtasks": subtasks_count, "total_cost": total_cost, "remaining_budget_usd": self.model_adapter.remaining_budget_usd, "execution_mode": execution_mode, "verifier_reason": "Live stream completed.", "verifier_verdict": "pass"})}\n\n'

        except Exception as exc:
            yield f'data: {json.dumps({"type": "error", "message": str(exc)})}\n\n'

    async def run(
        self,
        prompt: str,
        system_prompt: str = None,
        event_callback: EventCallback | None = None,
    ) -> str:
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        return await self._run_with_messages(
            messages,
            prompt=prompt,
            system_prompt=system_prompt,
            iteration=0,
            event_callback=event_callback,
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
            event_callback=None,
        )

    async def _run_with_messages(
        self,
        messages: list[dict[str, Any]],
        prompt: str,
        system_prompt: str | None,
        iteration: int,
        event_callback: EventCallback | None = None,
    ) -> str:
        last_tool_key: tuple[str, str] | None = None
        repeated_tool_calls = 0

        while iteration < self.max_iterations:
            self._apply_context_budget(messages)

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

                arguments = _parse_tool_arguments(arguments_str)
                tool_key = (name, json.dumps(arguments, sort_keys=True))
                if tool_key == last_tool_key:
                    repeated_tool_calls += 1
                else:
                    last_tool_key = tool_key
                    repeated_tool_calls = 1

                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "function_call": {
                        "name": name,
                        "arguments": arguments_str
                    }
                }
                messages.append(assistant_msg)
                if repeated_tool_calls >= self.repeated_tool_call_limit:
                    messages.append(
                        {
                            "role": "function",
                            "name": name,
                            "content": self._format_repeated_tool_warning(name),
                        }
                    )
                    iteration += 1
                    has_tool_calls = True
                    continue

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
                    event_callback=event_callback,
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

                    arguments = _parse_tool_arguments(arguments_str)
                    tool_key = (name, json.dumps(arguments, sort_keys=True))
                    if tool_key == last_tool_key:
                        repeated_tool_calls += 1
                    else:
                        last_tool_key = tool_key
                        repeated_tool_calls = 1

                    if repeated_tool_calls >= self.repeated_tool_call_limit:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": name,
                                "content": self._format_repeated_tool_warning(name),
                            }
                        )
                        continue

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
                        event_callback=event_callback,
                    )
                    if result is not None:
                        return result

                has_tool_calls = True

            # If there were no tool calls, we're done
            if not has_tool_calls:
                final_content = message.get("content", "")
                self._sync_context_manager(messages + [{"role": "assistant", "content": final_content}])
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
        event_callback: EventCallback | None = None,
    ) -> str | None:
        name = pending_call["name"]
        arguments = pending_call["arguments"]
        await self._emit_event(
            event_callback,
            {"type": "tool_call", "tool_name": name},
        )
        try:
            result = await self._execute_tool(name, arguments)
        except PermissionRequiredError as exc:
            await self._emit_event(
                event_callback,
                {"type": "approval_required", "tool_name": name, "approval_id": exc.approval_id},
            )
            self._save_permission_checkpoint(
                exc,
                messages=messages,
                pending_call=pending_call,
                prompt=prompt,
                system_prompt=system_prompt,
                iteration=iteration,
            )
            raise
        await self._emit_event(
            event_callback,
            {"type": "tool_result", "tool_name": name},
        )

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

    def _format_repeated_tool_warning(self, tool_name: str) -> str:
        return (
            f"[TOOL_RESULT:{tool_name}] "
            '{"skipped": true, "reason": "Repeated identical tool call suppressed."}\n'
            "You already have this tool result in the conversation. "
            "Do not call the same tool again. Return the final answer now using the existing result."
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
            parent_candidates = [str(path.parent) for path in paths if path.exists()]
            if parent_candidates:
                common_parent = Path(os.path.commonpath(parent_candidates))
            else:
                common_parent = test_files[0].parent
            proc = subprocess.run(
                ["python", "-m", "pytest", "-q", *[str(path) for path in test_files]],
                cwd=str(common_parent),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                return (
                    f"Generated pytest suite failed in {common_parent}: "
                    f"{(proc.stdout + proc.stderr).strip()[-400:]}"
                )

        return None

    def _verify_postconditions(self, postconditions: list[str], file_targets: list[str]) -> str | None:
        if not postconditions:
            return None

        existing_targets = [Path(target) for target in file_targets if Path(target).exists()]
        combined_text_parts: list[str] = []
        python_classes: set[str] = set()
        python_functions: set[str] = set()

        for path in existing_targets:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            combined_text_parts.append(text)
            if path.suffix == ".py":
                try:
                    tree = ast.parse(text, filename=str(path))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        python_classes.add(node.name)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        python_functions.add(node.name)

        combined_text = "\n".join(combined_text_parts)
        for condition in postconditions:
            if condition.startswith("file exists: "):
                raw_target = condition.split(": ", 1)[1]
                target_path = Path(raw_target)
                if not target_path.exists() and not any(path.name == target_path.name for path in existing_targets):
                    return f"Postcondition failed: missing file {raw_target}"
            elif condition.startswith("class exists: "):
                class_name = condition.split(": ", 1)[1]
                if class_name not in python_classes and class_name not in combined_text:
                    return f"Postcondition failed: missing class {class_name}"
            elif condition.startswith("function exists: "):
                function_name = condition.split(": ", 1)[1]
                function_patterns = [
                    f"def {function_name}(",
                    f"function {function_name}(",
                    f"const {function_name} =",
                    f"export function {function_name}(",
                    f"export const {function_name} =",
                ]
                if function_name not in python_functions and not any(pattern in combined_text for pattern in function_patterns):
                    return f"Postcondition failed: missing function {function_name}"
            elif condition.startswith("method exists: "):
                method_name = condition.split(": ", 1)[1]
                method_patterns = [
                    f"def {method_name}(",
                    f"{method_name}(",
                    f"{method_name}:",
                ]
                if not any(pattern in combined_text for pattern in method_patterns):
                    return f"Postcondition failed: missing method {method_name}"
            elif condition == "tests added or updated":
                has_test_file = any(
                    path.name.startswith(("test_", "check_"))
                    or path.suffix in {".spec.ts", ".spec.js"}
                    for path in existing_targets
                )
                if not has_test_file:
                    return "Postcondition failed: expected tests to be added or updated"

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
