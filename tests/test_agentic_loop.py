"""Tests for the agentic loop in QueryEngine.run_agentic()."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mantis.core.approval_store import ApprovalStore
from mantis.core.context_manager import ContextManager
from mantis.core.permissions import PermissionManager, PermissionRequiredError
from mantis.core.tool_registry import ToolRegistry
from mantis.core.query_engine import QueryEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(run_return: str = "ok") -> QueryEngine:
    """Create a QueryEngine with a mocked model adapter and tool registry."""
    model_adapter = MagicMock()
    tool_registry = MagicMock()
    tool_registry.list_schemas.return_value = []
    engine = QueryEngine(
        model_adapter=model_adapter,
        tool_registry=tool_registry,
        max_iterations=5,
    )
    # Patch the inner run() so tests don't hit the network
    engine.run = AsyncMock(return_value=run_return)
    return engine


# ---------------------------------------------------------------------------
# Test: multi-task split
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agentic_splits_multi_task():
    """'fix bug then write tests' should produce 2 subtasks (2 run() calls)."""
    engine = _make_engine("result")
    with patch("mantis.core.quality_gate.verify_output", return_value=(0.9, "good")):
        await engine.run_agentic("fix bug then write tests")

    # run() should have been called once per subtask
    assert engine.run.call_count == 2


# ---------------------------------------------------------------------------
# Test: quality gate is used
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agentic_uses_quality_gate():
    """run_agentic should call verify_output for each subtask."""
    engine = _make_engine("def test_foo(): pass")

    with patch("mantis.core.quality_gate.verify_output") as mock_verify:
        mock_verify.return_value = (0.9, "contains test def")
        result = await engine.run_agentic("write tests for auth module")

    assert mock_verify.called
    assert result  # non-empty output


# ---------------------------------------------------------------------------
# Test: multi-task returns combined output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agentic_combines_multi_task_output():
    """Multi-task plans must join results with the separator."""
    outputs = ["fix output", "test output"]
    call_count = 0

    async def side_effect(prompt, system_prompt=None):
        nonlocal call_count
        val = outputs[call_count % len(outputs)]
        call_count += 1
        return val

    engine = _make_engine()
    engine.run = side_effect

    with patch("mantis.core.quality_gate.verify_output", return_value=(0.9, "good")):
        result = await engine.run_agentic("fix bug then write tests")

    assert "---" in result
    assert "fix output" in result
    assert "test output" in result


# ---------------------------------------------------------------------------
# Test: single-task returns output directly (no separator)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agentic_single_task_no_separator():
    """Single-task plans must return output directly without a separator."""
    engine = _make_engine("only result")

    with patch("mantis.core.quality_gate.verify_output", return_value=(0.9, "good")):
        result = await engine.run_agentic("refactor the auth module")

    assert result == "only result"
    assert "---" not in result
    # run() should have been called exactly once
    assert engine.run.call_count == 1


@pytest.mark.asyncio
async def test_resume_from_approval_continues_checkpoint(tmp_path):
    model_adapter = MagicMock()
    model_adapter.chat = AsyncMock(
        side_effect=[
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_bash",
                                        "arguments": '{"command": "echo hi"}',
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {},
            },
            {
                "choices": [{"message": {"content": "final answer"}}],
                "usage": {},
            },
        ]
    )
    tool_registry = ToolRegistry()
    executed = []

    async def run_bash(command: str) -> str:
        executed.append(command)
        return "hi"

    tool_registry.register(
        "run_bash",
        "Run shell command",
        {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        run_bash,
    )

    approvals = ApprovalStore(tmp_path / "approvals")
    permission_manager = PermissionManager(mode="auto", approval_store=approvals)
    permission_manager.set_context(session_id="s1", job_id="j1")

    engine = QueryEngine(
        model_adapter=model_adapter,
        tool_registry=tool_registry,
        max_iterations=5,
        permission_manager=permission_manager,
    )

    with pytest.raises(PermissionRequiredError) as exc_info:
        await engine.run("run a command")

    approval = approvals.load(exc_info.value.approval_id)
    assert approval is not None
    assert approval.metadata["checkpoint"]["pending_call"]["name"] == "run_bash"

    approvals.update(approval.id, status="approved")
    result = await engine.resume_from_approval(approval.id)

    assert result == "final answer"
    assert executed == ["echo hi"]
    assert approvals.load(approval.id).status == "used"


def test_execute_pending_call_adds_explicit_tool_result_marker():
    model_adapter = MagicMock()
    tool_registry = ToolRegistry()

    async def read_file(file_path: str) -> str:
        return "hello"

    tool_registry.register(
        "read_file",
        "Read file",
        {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
        read_file,
    )
    engine = QueryEngine(model_adapter=model_adapter, tool_registry=tool_registry)
    messages: list[dict] = []

    asyncio.run(
        engine._execute_pending_call(
            messages,
            pending_call={
                "format": "tool_call",
                "call_id": "call-1",
                "name": "read_file",
                "arguments": {"file_path": "/tmp/demo.py"},
                "arguments_str": '{"file_path":"/tmp/demo.py"}',
                "tool_index": 0,
            },
            prompt="read file",
            system_prompt=None,
            iteration=0,
        )
    )

    assert messages[-1]["role"] == "tool"
    assert "[TOOL_RESULT:read_file]" in messages[-1]["content"]
    assert "Tool execution completed successfully." in messages[-1]["content"]


@pytest.mark.asyncio
async def test_run_agentic_retries_when_generated_checker_fails(tmp_path):
    target = tmp_path / "token_bucket.py"
    check = tmp_path / "check_token_bucket.py"
    responses = iter(
        [
            "first pass",
            "second pass",
        ]
    )

    prompts = []

    async def fake_run(prompt, system_prompt=None):
        prompts.append(prompt)
        text = next(responses)
        if text == "first pass":
            target.write_text("class TokenBucket:\n    pass\n", encoding="utf-8")
            check.write_text("raise SystemExit(1)\n", encoding="utf-8")
        else:
            target.write_text("class TokenBucket:\n    pass\n", encoding="utf-8")
            check.write_text("print('ok')\n", encoding="utf-8")
        return text

    engine = _make_engine()
    engine.run = fake_run

    async def _fake_cascade(*a, **kw):
        return (0.9, "good")

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan, \
         patch("mantis.core.quality_gate.verify_output", return_value=(0.9, "good")), \
         patch("mantis.core.quality_gate.verify_cascade", new=_fake_cascade):
        from mantis.core.planner import ExecutionPlan, PlannedTask

        mock_plan.return_value = ExecutionPlan(
            task_type="feature",
            complexity="medium",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[
                PlannedTask(
                    title="t",
                    prompt=f"Create {target} and {check}",
                    task_type="feature",
                    file_targets=[str(target), str(check)],
                )
            ],
        )
        result = await engine.run_agentic("Create token bucket")

    assert result == "second pass"
    assert "[ARTIFACT VERIFICATION FEEDBACK]" in prompts[-1]
    assert f"[FILE: {check}]" in prompts[-1]
    assert "Satisfy the generated checks exactly" in prompts[-1]


@pytest.mark.asyncio
async def test_run_agentic_adds_semantic_guidance_for_python_tasks(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text(
        "def planner_fix():\n    return 'before'\n",
        encoding="utf-8",
    )
    captured_prompts: list[str] = []

    async def fake_run(prompt, system_prompt=None):
        captured_prompts.append(prompt)
        return "updated"

    engine = _make_engine()
    engine.run = fake_run

    async def _fake_cascade(*a, **kw):
        return (0.9, "good")

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan, \
         patch("mantis.core.quality_gate.verify_output", return_value=(0.9, "good")), \
         patch("mantis.core.quality_gate.verify_cascade", new=_fake_cascade):
        from mantis.core.planner import ExecutionPlan, PlannedTask

        mock_plan.return_value = ExecutionPlan(
            task_type="bug_fix",
            complexity="low",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[
                PlannedTask(
                    title="fix sample",
                    prompt=f"fix {target}",
                    task_type="bug_fix",
                    file_targets=[str(target)],
                )
            ],
        )

        await engine.run_agentic(f"fix {target}")

    assert len(captured_prompts) == 1
    enriched = captured_prompts[0]
    assert "[PYTHON EDIT STRATEGY]" in enriched
    assert "list_python_symbols" in enriched
    assert "replace_python_symbol" in enriched


@pytest.mark.asyncio
async def test_run_agentic_adds_semantic_guidance_for_js_tasks(tmp_path):
    engine = _make_engine("result")
    target = tmp_path / "auth.ts"
    target.write_text("export class AuthService {}\n", encoding="utf-8")

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan:
        mock_plan.return_value.tasks = [
            MagicMock(
                prompt="add createSession to auth flow",
                file_targets=[str(target)],
                task_type="feature",
                needs_escalation=False,
                postconditions=[],
            )
        ]
        mock_plan.return_value.complexity = "medium"
        with patch("mantis.core.quality_gate.verify_output", return_value=(0.9, "good")):
            await engine.run_agentic("add createSession to auth flow")

    enriched = engine.run.call_args.args[0]
    assert "[JS/TS EDIT STRATEGY]" in enriched
    assert "list_js_symbols" in enriched
    assert "read_js_symbol" in enriched


@pytest.mark.asyncio
async def test_run_streaming_adds_semantic_guidance_for_python_tasks(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text(
        "def planner_fix():\n    return 'before'\n",
        encoding="utf-8",
    )

    model_adapter = MagicMock()
    model_adapter.total_cost_usd = 0.0
    model_adapter.remaining_budget_usd = None
    tool_registry = MagicMock()
    engine = QueryEngine(
        model_adapter=model_adapter,
        tool_registry=tool_registry,
        max_iterations=5,
    )
    engine.run_agentic = AsyncMock(return_value="done")

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan:
        from mantis.core.planner import ExecutionPlan, PlannedTask

        mock_plan.return_value = ExecutionPlan(
            task_type="bug_fix",
            complexity="low",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[
                PlannedTask(
                    title="fix sample",
                    prompt=f"fix {target}",
                    task_type="bug_fix",
                    file_targets=[str(target)],
                )
            ],
        )

        chunks = []
        async for chunk in engine.run_streaming(f"fix {target}"):
            chunks.append(chunk)

    assert chunks
    engine.run_agentic.assert_awaited_once()
    user_message = engine.run_agentic.await_args.args[0]
    assert "[PYTHON EDIT STRATEGY]" in user_message
    assert "replace_python_symbol" in user_message


@pytest.mark.asyncio
async def test_run_streaming_uses_agent_run_not_raw_model_stream():
    model_adapter = MagicMock()
    model_adapter.stream = AsyncMock()
    model_adapter.total_cost_usd = 0.0
    model_adapter.remaining_budget_usd = None
    tool_registry = MagicMock()
    engine = QueryEngine(
        model_adapter=model_adapter,
        tool_registry=tool_registry,
        max_iterations=5,
    )
    engine.run_agentic = AsyncMock(return_value="final answer")

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan:
        from mantis.core.planner import ExecutionPlan, PlannedTask

        mock_plan.return_value = ExecutionPlan(
            task_type="unknown",
            complexity="low",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[
                PlannedTask(
                    title="answer",
                    prompt="answer cleanly",
                    task_type="unknown",
                    file_targets=[],
                )
            ],
        )

        chunks = []
        async for chunk in engine.run_streaming("answer cleanly"):
            chunks.append(chunk)

    assert chunks
    engine.run_agentic.assert_awaited_once()
    model_adapter.stream.assert_not_called()
    assert any('"content": "final "' in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_run_streaming_emits_tool_progress_events():
    model_adapter = MagicMock()
    model_adapter.stream = AsyncMock()
    model_adapter.total_cost_usd = 0.0
    model_adapter.remaining_budget_usd = None
    tool_registry = MagicMock()
    engine = QueryEngine(
        model_adapter=model_adapter,
        tool_registry=tool_registry,
        max_iterations=5,
    )

    async def fake_run_agentic(prompt, system_prompt=None, event_callback=None):
        await event_callback({"type": "tool_call", "tool_name": "read_file"})
        await asyncio.sleep(0)
        await event_callback({"type": "tool_result", "tool_name": "read_file"})
        return "final answer"

    engine.run_agentic = AsyncMock(side_effect=fake_run_agentic)

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan:
        from mantis.core.planner import ExecutionPlan, PlannedTask

        mock_plan.return_value = ExecutionPlan(
            task_type="unknown",
            complexity="low",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[
                PlannedTask(
                    title="answer",
                    prompt="answer cleanly",
                    task_type="unknown",
                    file_targets=[],
                )
            ],
        )

        chunks = []
        async for chunk in engine.run_streaming("answer cleanly"):
            chunks.append(chunk)

    assert any("Using tool: read_file" in chunk for chunk in chunks)
    assert any("Completed tool: read_file" in chunk for chunk in chunks)
    assert any('"content": "final "' in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_run_suppresses_repeated_identical_tool_calls():
    model_adapter = MagicMock()
    model_adapter.chat = AsyncMock(
        side_effect=[
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"file_path": "demo.py"}',
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {},
            },
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"file_path": "demo.py"}',
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {},
            },
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-3",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"file_path": "demo.py"}',
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {},
            },
            {
                "choices": [{"message": {"content": "4"}}],
                "usage": {},
            },
        ]
    )
    tool_registry = ToolRegistry()
    executed: list[str] = []

    async def read_file(file_path: str) -> str:
        executed.append(file_path)
        return "def test_a(): pass\ndef test_b(): pass"

    tool_registry.register(
        "read_file",
        "Read file",
        {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
        read_file,
    )

    engine = QueryEngine(model_adapter=model_adapter, tool_registry=tool_registry, max_iterations=6)
    result = await engine.run("count tests")

    assert result == "4"
    assert executed == ["demo.py"]


@pytest.mark.asyncio
async def test_run_agentic_returns_when_run_times_out_but_artifacts_pass(tmp_path):
    target = tmp_path / "binary_search.py"
    test_file = tmp_path / "test_binary_search.py"

    async def fake_run(prompt, system_prompt=None):
        target.write_text("def binary_search(arr, target):\n    return -1\n", encoding="utf-8")
        test_file.write_text(
            "from binary_search import binary_search\n\n"
            "def test_missing():\n"
            "    assert binary_search([], 3) == -1\n",
            encoding="utf-8",
        )
        raise asyncio.TimeoutError

    engine = _make_engine()
    engine.run = fake_run

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan, patch(
        "mantis.core.quality_gate.verify_output", return_value=(0.9, "good")
    ):
        from mantis.core.planner import ExecutionPlan, PlannedTask

        mock_plan.return_value = ExecutionPlan(
            task_type="test_writing",
            complexity="medium",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[
                PlannedTask(
                    title="t",
                    prompt=f"Create {target} and {test_file}",
                    task_type="test_writing",
                    file_targets=[str(target), str(test_file)],
                )
            ],
        )
        result = await engine.run_agentic("Create binary search and tests")

    assert "artifact checks passed" in result


@pytest.mark.asyncio
async def test_run_agentic_records_postcondition_success(tmp_path):
    target = tmp_path / "token_bucket.py"
    helper = tmp_path / "helpers.py"

    async def fake_run(prompt, system_prompt=None):
        target.write_text(
            "class TokenBucket:\n"
            "    def __init__(self):\n"
            "        self.tokens = 1\n",
            encoding="utf-8",
        )
        helper.write_text(
            "def allow(tokens: int = 1):\n"
            "    return True\n\n"
            "def available():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        return "written and saved successfully"

    engine = _make_engine()
    engine.run = fake_run

    async def _fake_cascade(*a, **kw):
        return (0.9, "good")

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan, \
         patch("mantis.core.quality_gate.verify_output", return_value=(0.9, "good")), \
         patch("mantis.core.quality_gate.verify_cascade", new=_fake_cascade):
        from mantis.core.planner import ExecutionPlan, PlannedTask

        mock_plan.return_value = ExecutionPlan(
            task_type="feature",
            complexity="medium",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[
                PlannedTask(
                    title="token bucket",
                    prompt=f"Create {target} and {helper}",
                    task_type="feature",
                    file_targets=[str(target), str(helper)],
                    postconditions=[
                        f"file exists: {target}",
                        "class exists: TokenBucket",
                        "method exists: allow",
                        "method exists: available",
                    ],
                )
            ],
        )
        await engine.run_agentic("Create token bucket")

    task = engine.last_run_details["tasks"][0]
    assert task["postcondition_check"]["ok"] is True
    assert engine.last_run_details["verifier"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_run_agentic_fails_when_postconditions_still_missing_after_retry(tmp_path):
    target = tmp_path / "token_bucket.py"

    prompts: list[str] = []

    async def fake_run(prompt, system_prompt=None):
        prompts.append(prompt)
        target.write_text(
            "class WrongName:\n"
            "    def available(self):\n"
            "        return 1\n",
            encoding="utf-8",
        )
        return "written and saved successfully"

    engine = _make_engine()
    engine.run = fake_run

    async def _fake_cascade(*a, **kw):
        return (0.9, "good")

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan, \
         patch("mantis.core.quality_gate.verify_output", return_value=(0.9, "good")), \
         patch("mantis.core.quality_gate.verify_cascade", new=_fake_cascade):
        from mantis.core.planner import ExecutionPlan, PlannedTask

        mock_plan.return_value = ExecutionPlan(
            task_type="feature",
            complexity="low",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[
                PlannedTask(
                    title="token bucket",
                    prompt=f"Create {target}",
                    task_type="feature",
                    file_targets=[str(target)],
                    postconditions=[
                        f"file exists: {target}",
                        "class exists: TokenBucket",
                        "method exists: allow",
                    ],
                )
            ],
        )
        await engine.run_agentic("Create token bucket")

    task = engine.last_run_details["tasks"][0]
    assert task["postcondition_check"]["ok"] is False
    assert task["status"] == "failed"
    assert engine.last_run_details["verifier"]["verdict"] == "fail"
    assert "[POSTCONDITIONS]" in prompts[-1]


@pytest.mark.asyncio
async def test_run_records_context_trim_metrics():
    model_adapter = MagicMock()
    model_adapter.chat = AsyncMock(
        return_value={
            "choices": [{"message": {"content": "final answer"}}],
            "usage": {},
        }
    )
    tool_registry = MagicMock()
    tool_registry.list_schemas.return_value = []
    engine = QueryEngine(
        model_adapter=model_adapter,
        tool_registry=tool_registry,
        max_iterations=2,
        context_manager=ContextManager(max_tokens=40),
    )

    result = await engine._run_with_messages(
        [
            {"role": "system", "content": "system " * 20},
            {"role": "user", "content": "a" * 200},
            {"role": "assistant", "content": "b" * 200},
            {"role": "user", "content": "c" * 200},
        ],
        prompt="c" * 200,
        system_prompt="system " * 20,
        iteration=0,
    )

    assert result == "final answer"
    metrics = engine._context_metrics
    assert metrics["messages_before_trim"] >= metrics["messages_after_trim"]
    assert metrics["messages_dropped"] >= 1
