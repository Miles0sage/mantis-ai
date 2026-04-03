from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mantis.agents.orchestrator import CoordinatorOrchestrator
from mantis.core.planner import ExecutionPlan, PlannedTask
from mantis.core.system_prompt import build_role_prompt


def test_build_role_prompt_includes_role():
    prompt = build_role_prompt("verifier", project_instructions="Use pytest", cost_aware=True)
    assert "ROLE: VERIFIER" in prompt
    assert "Use pytest" in prompt
    assert "MODEL ROUTING" in prompt


@pytest.mark.asyncio
async def test_orchestrator_runs_verifier_pass():
    model_adapter = MagicMock()
    model_adapter.chat = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {
                        "content": '{"verdict":"pass","reason":"looks good","missing":[]}'
                    }
                }
            ]
        }
    )
    tool_registry = MagicMock()
    orchestrator = CoordinatorOrchestrator(model_adapter=model_adapter, tool_registry=tool_registry)
    orchestrator._run_workers = AsyncMock(return_value=["worker output"])

    plan = ExecutionPlan(
        task_type="feature",
        complexity="high",
        can_run_in_parallel=False,
        needs_escalation=True,
        tasks=[PlannedTask(title="t", prompt="do thing", task_type="feature")],
    )

    result = await orchestrator.execute("do thing", plan)

    assert result.output == "worker output"
    assert result.verification.verdict == "pass"
    assert result.revised is False


@pytest.mark.asyncio
async def test_orchestrator_artifact_verifier_rejects_missing_interface(tmp_path):
    model_adapter = MagicMock()
    model_adapter.chat = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {
                        "content": '{"verdict":"pass","reason":"looks fine","missing":[]}'
                    }
                }
            ]
        }
    )
    tool_registry = MagicMock()
    orchestrator = CoordinatorOrchestrator(model_adapter=model_adapter, tool_registry=tool_registry)

    target = tmp_path / "token_bucket.py"
    target.write_text("class WrongName:\n    pass\n", encoding="utf-8")
    plan = ExecutionPlan(
        task_type="feature",
        complexity="high",
        can_run_in_parallel=False,
        needs_escalation=True,
        tasks=[PlannedTask(title="t", prompt="do thing", task_type="feature", file_targets=[str(target)])],
    )

    verification = await orchestrator._verify(
        f"Create {target} implementing a TokenBucket class with methods __init__, allow(tokens: int = 1), available().",
        "done",
        plan,
    )

    assert verification.verdict == "fail"
    assert any("TokenBucket" in item for item in verification.missing)


@pytest.mark.asyncio
async def test_orchestrator_rejects_nondeterministic_exactness_mismatch(tmp_path):
    model_adapter = MagicMock()
    model_adapter.chat = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {
                        "content": '{"verdict":"pass","reason":"looks fine","missing":[]}'
                    }
                }
            ]
        }
    )
    tool_registry = MagicMock()
    orchestrator = CoordinatorOrchestrator(model_adapter=model_adapter, tool_registry=tool_registry)

    impl = tmp_path / "token_bucket.py"
    check = tmp_path / "check_token_bucket.py"
    impl.write_text(
        "import time\n\nclass TokenBucket:\n    def available(self):\n        return time.time()\n",
        encoding="utf-8",
    )
    check.write_text(
        "from token_bucket import TokenBucket\nassert TokenBucket().available() == 1.0\n",
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        task_type="feature",
        complexity="high",
        can_run_in_parallel=False,
        needs_escalation=True,
        tasks=[
            PlannedTask(
                title="t",
                prompt="do thing",
                task_type="feature",
                file_targets=[str(impl), str(check)],
            )
        ],
    )

    verification = await orchestrator._verify(
        f"Create {impl} implementing a TokenBucket class with methods __init__, allow(tokens: int = 1), available(). Also create {check}. Do not run the check.",
        "done",
        plan,
    )

    assert verification.verdict == "fail"
    assert any("nondeterministic exactness mismatch" in item for item in verification.missing)


@pytest.mark.asyncio
async def test_orchestrator_rejects_failing_generated_checker(tmp_path):
    model_adapter = MagicMock()
    model_adapter.chat = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {
                        "content": '{"verdict":"pass","reason":"looks fine","missing":[]}'
                    }
                }
            ]
        }
    )
    tool_registry = MagicMock()
    orchestrator = CoordinatorOrchestrator(model_adapter=model_adapter, tool_registry=tool_registry)

    impl = tmp_path / "token_bucket.py"
    check = tmp_path / "check_token_bucket.py"
    impl.write_text("class TokenBucket:\n    pass\n", encoding="utf-8")
    check.write_text("raise SystemExit(1)\n", encoding="utf-8")
    plan = ExecutionPlan(
        task_type="feature",
        complexity="high",
        can_run_in_parallel=False,
        needs_escalation=True,
        tasks=[
            PlannedTask(
                title="t",
                prompt="do thing",
                task_type="feature",
                file_targets=[str(impl), str(check)],
            )
        ],
    )

    verification = await orchestrator._verify(
        f"Create {impl} implementing a TokenBucket class. Also create {check}. Do not run the check.",
        "done",
        plan,
    )

    assert verification.verdict == "fail"
    assert any("generated checker failed" in item for item in verification.missing)
