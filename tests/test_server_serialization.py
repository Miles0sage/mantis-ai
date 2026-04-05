import asyncio
from types import SimpleNamespace

from mantis.core.trace_store import TraceStore
from mantis.server import _extract_execution_summary, _serialize_job, list_traces


def test_extract_execution_summary_flattens_execution_payload():
    stats = {
        "execution": {
            "execution_mode": "coordinator_worker_verifier",
            "tasks": [{"title": "plan", "task_type": "feature"}],
            "verifier": {"verdict": "pass", "reason": "All checks passed."},
            "context": {"max_messages_dropped": 2},
            "workers": [{"agent_id": "worker-1", "title": "task one", "changed_files": ["app.py"], "diff_preview": "diff --git", "resume_metadata": {"resume_key": "worker-1", "prompt": "fix app", "execution_prompt": "fix app", "resumable": True}}],
            "pr_review": {"title": "Add retry flow", "verdict": "pass"},
            "worktree": {"branch": "mantis/issue-12-add-retry-flow", "path": "/tmp/wt"},
            "draft_pr": {"status": "created", "url": "https://github.com/acme/api/pull/12"},
        }
    }

    summary = _extract_execution_summary(stats)

    assert summary["execution_mode"] == "coordinator_worker_verifier"
    assert summary["tasks"][0]["task_type"] == "feature"
    assert summary["verification"]["verdict"] == "pass"
    assert summary["context"]["max_messages_dropped"] == 2
    assert summary["workers"][0]["agent_id"] == "worker-1"
    assert summary["workers"][0]["changed_files"] == ["app.py"]
    assert summary["workers"][0]["resume_metadata"]["resume_key"] == "worker-1"
    assert summary["pr_review"]["title"] == "Add retry flow"
    assert summary["worktree"]["branch"] == "mantis/issue-12-add-retry-flow"
    assert summary["draft_pr"]["status"] == "created"


def test_serialize_job_exposes_top_level_verification_and_tasks():
    job = SimpleNamespace(
        to_dict=lambda: {
            "id": "job-1",
            "prompt": "build feature",
            "status": "done",
            "metadata": {
                "plan": {"tasks": [{"title": "fallback plan", "task_type": "feature"}]},
                "execution": {
                    "execution_mode": "direct_agentic",
                    "tasks": [{"title": "real task", "task_type": "bug_fix", "status": "done"}],
                    "verifier": {"verdict": "pass", "reason": "Verified."},
                    "context": {"last_trim": {"messages_dropped": 1}},
                    "workers": [{"agent_id": "worker-1", "worktree": {"branch": "mantis/task-1"}, "changed_files": ["app/service.py"], "diff_preview": "diff --git", "resume_metadata": {"resume_key": "worker-1", "prompt": "fix service", "execution_prompt": "fix service", "resumable": True}}],
                    "pr_review": {"title": "Retry flow", "changed_files": ["app/service.py"]},
                    "worktree": {"branch": "mantis/issue-12-retry-flow", "path": "/tmp/wt"},
                    "draft_pr": {"status": "created", "url": "https://github.com/acme/api/pull/22"},
                },
            },
        }
    )

    payload = _serialize_job(job)

    assert payload["execution_mode"] == "direct_agentic"
    assert payload["verification"]["verdict"] == "pass"
    assert payload["tasks"][0]["title"] == "real task"
    assert payload["context"]["last_trim"]["messages_dropped"] == 1
    assert payload["workers"][0]["worktree"]["branch"] == "mantis/task-1"
    assert payload["workers"][0]["changed_files"] == ["app/service.py"]
    assert payload["workers"][0]["resume_metadata"]["resumable"] is True
    assert payload["pr_review"]["title"] == "Retry flow"
    assert payload["worktree"]["branch"] == "mantis/issue-12-retry-flow"
    assert payload["draft_pr"]["status"] == "created"


def test_serialize_job_exposes_approval_and_resume_summary():
    job = SimpleNamespace(
        to_dict=lambda: {
            "id": "job-2",
            "prompt": "refactor auth",
            "status": "running",
            "metadata": {
                "approval_id": "appr-1",
                "tool_name": "run_bash",
                "risk_level": "HIGH",
                "resumed_from_approval_id": "appr-1",
                "resumed_tool_name": "run_bash",
                "resume_note": "allow it",
                "execution": {},
            },
        }
    )

    payload = _serialize_job(job)

    assert payload["approval"]["approval_id"] == "appr-1"
    assert payload["approval"]["tool_name"] == "run_bash"
    assert payload["resume"]["approval_id"] == "appr-1"
    assert payload["resume"]["note"] == "allow it"


def test_traces_endpoint_lists_session_traces(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    store = TraceStore()
    store.create(
        session_id="s1",
        prompt="fix auth flow",
        response="done",
        model="gpt-4o-mini",
        provider="openai-compatible",
        stats={"execution": {"verifier": {"verdict": "pass"}}},
    )
    store.create(
        session_id="other",
        prompt="ignore me",
        response="x",
    )

    payload = asyncio.run(list_traces(session_id="s1", limit=10))
    assert len(payload["traces"]) == 1
    assert payload["traces"][0]["prompt"] == "fix auth flow"


def test_traces_endpoint_filters_by_execution_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    store = TraceStore()
    store.create(
        session_id="s1",
        prompt="refactor auth flow",
        response="done",
        stats={
            "routing": {"task_type": "refactor"},
            "execution": {
                "execution_mode": "coordinator_worker_verifier",
                "verifier": {"verdict": "pass"},
            },
        },
    )
    store.create(
        session_id="s1",
        prompt="read file",
        response="ok",
        stats={
            "routing": {"task_type": "review"},
            "execution": {
                "execution_mode": "local_fast_path",
                "verifier": {"verdict": "pass"},
            },
        },
    )

    payload = asyncio.run(list_traces(session_id="s1", execution_mode="coordinator_worker_verifier", limit=10))
    assert len(payload["traces"]) == 1
    assert payload["traces"][0]["execution_mode"] == "coordinator_worker_verifier"
