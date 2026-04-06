from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mantis.agents.orchestrator import CoordinatorOrchestrator
from mantis.agents.spawner import AgentResult
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


def test_prepare_worker_task_rewrites_paths_into_isolated_worktree(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    task = PlannedTask(
        title="Edit service",
        prompt="Fix app/service.py and keep tests/test_service.py passing.",
        task_type="bug_fix",
        file_targets=["app/service.py", "tests/test_service.py"],
        dependencies=["Read spec"],
        parallel_group="group-1",
    )
    orchestrator = CoordinatorOrchestrator(
        model_adapter=MagicMock(),
        tool_registry=MagicMock(),
        project_dir=str(repo_dir),
    )

    monkeypatch.setattr("mantis.agents.orchestrator.is_git_repo", lambda repo_dir: True)
    monkeypatch.setattr(
        "mantis.agents.orchestrator.create_issue_worktree",
        lambda repo_dir, title, root_dir=None: {
            "repo_dir": repo_dir,
            "worktree_dir": str(tmp_path / "wt"),
            "branch": "mantis/task-worker-1-edit-service-abcd1234",
            "base_branch": "HEAD",
        },
    )

    prepared = orchestrator._prepare_worker_task(task, 1)

    assert "[WORKER ISOLATION]" in prepared["prompt"]
    assert str(tmp_path / "wt" / "app" / "service.py") in prepared["prompt"]
    assert prepared["default_bash_cwd"] == str(tmp_path / "wt")
    assert prepared["metadata"]["worktree"]["branch"].startswith("mantis/task-worker-1")
    assert prepared["metadata"]["file_targets"][0] == str(tmp_path / "wt" / "app" / "service.py")
    assert prepared["metadata"]["dependencies"] == ["Read spec"]
    assert prepared["metadata"]["resume_metadata"]["resume_key"] == "worker-1"
    assert prepared["metadata"]["resume_metadata"]["resumable"] is True
    assert prepared["metadata"]["resume_metadata"]["prompt"] == "Fix app/service.py and keep tests/test_service.py passing."
    assert "[WORKER ISOLATION]" in prepared["metadata"]["resume_metadata"]["execution_prompt"]


def test_orchestrator_detects_overlapping_targets(tmp_path):
    orchestrator = CoordinatorOrchestrator(
        model_adapter=MagicMock(),
        tool_registry=MagicMock(),
        project_dir=str(tmp_path),
    )
    prepared = [
        {
            "metadata": {
                "file_targets": [str(tmp_path / "app.py"), str(tmp_path / "tests" / "test_app.py")],
            }
        },
        {
            "metadata": {
                "file_targets": [str(tmp_path / "app.py")],
            }
        },
    ]

    assert orchestrator._has_overlapping_targets(prepared) is True


def test_orchestrator_enriches_worker_result_with_git_review(tmp_path, monkeypatch):
    orchestrator = CoordinatorOrchestrator(
        model_adapter=MagicMock(),
        tool_registry=MagicMock(),
        project_dir=str(tmp_path),
    )
    result = AgentResult(
        agent_id="worker-1",
        task="do thing",
        output="ok",
        status="completed",
        duration_ms=20.0,
        token_usage={},
        metadata={
            "project_dir": str(tmp_path / "wt"),
            "worktree": {"branch": "mantis/task-1", "worktree_dir": str(tmp_path / "wt")},
        },
    )

    monkeypatch.setattr(
        "mantis.agents.orchestrator.collect_git_review",
        lambda repo_dir: {
            "branch": "mantis/task-1",
            "path": str(tmp_path / "wt"),
            "changed_files": ["app.py", "tests/test_app.py"],
            "diff": "diff --git a/app.py b/app.py\n+return 2\n",
        },
    )

    orchestrator._enrich_worker_result(result)

    assert result.metadata["changed_files"] == ["app.py", "tests/test_app.py"]
    assert "diff --git" in result.metadata["diff_preview"]
    assert result.metadata["worktree"]["path"] == str(tmp_path / "wt")


@pytest.mark.asyncio
async def test_orchestrator_execute_returns_worker_metadata():
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
    orchestrator._last_worker_results = [
        AgentResult(
            agent_id="worker-1",
            task="do thing",
            output="worker output",
            status="completed",
            duration_ms=123.4,
            token_usage={"cost": 0.01},
            metadata={
                "task_index": 1,
                "title": "t",
                "task_type": "feature",
                "dependencies": [],
                "parallel_group": "serial",
                "file_targets": ["app.py"],
                "project_dir": "/tmp/wt",
                "worktree": {"branch": "mantis/task-1", "worktree_dir": "/tmp/wt"},
                "changed_files": ["app.py"],
                "diff_preview": "diff --git a/app.py b/app.py\n+return 2\n",
                "resume_metadata": {
                    "resume_key": "worker-1",
                    "task_index": 1,
                    "title": "t",
                    "prompt": "do thing",
                    "execution_prompt": "do thing",
                    "file_targets": ["app.py"],
                    "dependencies": [],
                    "project_dir": "/tmp/wt",
                    "worktree_branch": "mantis/task-1",
                    "worktree_dir": "/tmp/wt",
                    "resumable": True,
                },
            },
        )
    ]

    plan = ExecutionPlan(
        task_type="feature",
        complexity="high",
        can_run_in_parallel=False,
        needs_escalation=True,
        tasks=[PlannedTask(title="t", prompt="do thing", task_type="feature")],
    )

    result = await orchestrator.execute("do thing", plan)

    assert result.workers[0]["agent_id"] == "worker-1"
    assert result.workers[0]["worktree"]["branch"] == "mantis/task-1"
    assert result.workers[0]["project_dir"] == "/tmp/wt"
    assert result.workers[0]["changed_files"] == ["app.py"]
    assert result.workers[0]["resume_metadata"]["resume_key"] == "worker-1"
    assert result.worker_summary["worker_count"] == 1
    assert result.worker_summary["completed_workers"] == 1
    assert result.worker_summary["changed_files"] == ["app.py"]
    assert result.worker_summary["total_cost"] == 0.01
