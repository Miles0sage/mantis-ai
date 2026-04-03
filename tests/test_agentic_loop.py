"""Tests for the agentic loop in QueryEngine.run_agentic()."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mantis.core.approval_store import ApprovalStore
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

    with patch("mantis.core.query_engine.build_execution_plan") as mock_plan, patch(
        "mantis.core.quality_gate.verify_output", return_value=(0.9, "good")
    ):
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
