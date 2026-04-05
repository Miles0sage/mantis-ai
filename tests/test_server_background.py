from __future__ import annotations

import asyncio
import threading
import json

import httpx
import pytest

from mantis.server import app


async def _poll_job(client: httpx.AsyncClient, job_id: str, target_statuses: set[str], attempts: int = 40):
    final_job = None
    for _ in range(attempts):
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        final_job = response.json()
        if final_job["status"] in target_statuses:
            return final_job
        await asyncio.sleep(0.05)
    return final_job


@pytest.mark.asyncio
async def test_background_job_completes_with_async_client(tmp_path, monkeypatch):
    target = tmp_path / "edit_me.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")

    async def fake_run_chat(self, prompt: str, job_id: str | None = None) -> str:
        target.write_text("def value():\n    return 2\n", encoding="utf-8")
        return "updated"

    monkeypatch.setenv("MANTIS_API_KEY", "test-key")
    monkeypatch.setattr("mantis.app.MantisApp._run_chat", fake_run_chat)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/jobs",
            json={"prompt": f"edit {target}", "session_id": "bg-test"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        final_job = await _poll_job(client, job_id, {"done", "failed"})

    assert final_job is not None
    assert final_job["status"] == "done"
    assert target.read_text(encoding="utf-8") == "def value():\n    return 2\n"


@pytest.mark.asyncio
async def test_background_job_waits_for_model_escalation_approval_async(tmp_path, monkeypatch):
    async def fake_run_agentic(self, prompt: str, system_prompt: str | None = None) -> str:
        return "escalated result"

    def fake_route(self, prompt: str):
        profile = next(model for model in self.router.list_models() if model.name == "claude-3-5-sonnet")
        return profile, {
            "strategy": "auto_plan_router",
            "task_type": "feature",
            "complexity": "high",
            "file_count": 3,
            "task_count": 2,
            "needs_escalation": True,
        }

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "mantis.server._resolve_config",
        lambda overrides=None: {
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "openai_api_key": "test-openai",
            "anthropic_api_key": "test-anthropic",
            "budget_usd": None,
            "explicit_model": False,
        },
    )
    monkeypatch.setattr("mantis.app.MantisApp._resolve_model_for_prompt", fake_route)
    monkeypatch.setattr("mantis.app.MantisApp._should_use_orchestrator", lambda self, routing: False)
    monkeypatch.setattr("mantis.core.query_engine.QueryEngine.run_agentic", fake_run_agentic)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/jobs",
            json={"prompt": "refactor a.py and b.py and c.py", "session_id": "bg-model-approval"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        waiting_job = await _poll_job(client, job_id, {"awaiting_approval", "done", "failed"})
        assert waiting_job is not None
        assert waiting_job["status"] == "awaiting_approval"
        assert waiting_job["metadata"]["tool_name"] == "model_escalation"
        approval_id = waiting_job["metadata"]["approval_id"]

        approve = await client.post(
            f"/api/approvals/{approval_id}/approve",
            json={"note": "allow stronger model"},
        )
        assert approve.status_code == 200

        final_job = await _poll_job(client, job_id, {"done", "failed"})

    assert final_job is not None
    assert final_job["status"] == "done"
    assert final_job["response"] == "escalated result"


@pytest.mark.asyncio
async def test_background_job_exposes_worktree_and_pr_review_metadata(tmp_path, monkeypatch):
    target = tmp_path / "service.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")

    async def fake_run_chat(self, prompt: str, job_id: str | None = None) -> str:
        self.last_stats = {
            "model": "gpt-4o-mini",
            "provider": "openai-compatible",
            "execution": {
                "execution_mode": "direct_agentic",
                "tasks": [{"title": "edit service", "file_targets": [str(target)]}],
                "verifier": {"verdict": "pass", "reason": "Verified."},
            },
        }
        target.write_text("def value():\n    return 2\n", encoding="utf-8")
        return "updated"

    monkeypatch.setenv("MANTIS_API_KEY", "test-key")
    monkeypatch.setattr("mantis.app.MantisApp._run_chat", fake_run_chat)
    monkeypatch.setattr(
        "mantis.server.create_issue_worktree",
        lambda repo_dir, title, issue_number=None, root_dir=None: {
            "repo_dir": repo_dir,
            "worktree_dir": str(tmp_path / "wt"),
            "branch": "mantis/issue-12-add-retry-flow",
            "base_branch": "HEAD",
        },
    )
    monkeypatch.setattr(
        "mantis.server.collect_git_review",
        lambda repo_dir: {
            "branch": "mantis/issue-12-add-retry-flow",
            "path": str(tmp_path / "wt"),
            "changed_files": [str(target)],
            "diff": "diff --git a/service.py b/service.py\n+return 2\n",
        },
    )
    monkeypatch.setattr(
        "mantis.server._create_draft_pr_with_gh",
        lambda title, body, branch, repo_name=None: "https://github.com/acme/api/pull/99",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/jobs",
            json={
                "prompt": f"edit {target}",
                "session_id": "bg-issue-pr",
                "issue_title": "Add retry flow",
                "issue_number": 12,
                "repo_name": "acme/api",
                "use_worktree": True,
                "worktree_root_dir": str(tmp_path / "worktrees"),
                "create_draft_pr": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        final_job = await _poll_job(client, job_id, {"done", "failed"})

    assert final_job is not None
    assert final_job["status"] == "done"
    assert final_job["worktree"]["branch"] == "mantis/issue-12-add-retry-flow"
    assert final_job["pr_review"]["title"] == "[Issue #12] Add retry flow"
    assert str(target) in final_job["pr_review"]["changed_files"]
    assert "diff --git" in final_job["pr_review"]["diff_preview"]
    assert final_job["draft_pr"]["status"] == "created"
    assert final_job["draft_pr"]["url"] == "https://github.com/acme/api/pull/99"


@pytest.mark.asyncio
async def test_background_job_uses_explicit_repo_dir_for_worktree(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    async def fake_run_chat(self, prompt: str, job_id: str | None = None) -> str:
        self.last_stats = {
            "model": "gpt-4o-mini",
            "provider": "openai-compatible",
            "execution": {
                "execution_mode": "direct_agentic",
                "tasks": [],
                "verifier": {"verdict": "pass", "reason": "Verified."},
            },
        }
        return "updated"

    create_calls = []
    monkeypatch.setenv("MANTIS_API_KEY", "test-key")
    monkeypatch.setattr("mantis.app.MantisApp._run_chat", fake_run_chat)

    def fake_create_issue_worktree(repo_dir, title, issue_number=None, root_dir=None):
        create_calls.append(repo_dir)
        return {
            "repo_dir": repo_dir,
            "worktree_dir": str(tmp_path / "wt"),
            "branch": "mantis/issue-2-week-2-real-run",
            "base_branch": "HEAD",
        }

    monkeypatch.setattr("mantis.server.create_issue_worktree", fake_create_issue_worktree)
    monkeypatch.setattr(
        "mantis.server.collect_git_review",
        lambda repo_dir: {
            "branch": "mantis/issue-2-week-2-real-run",
            "path": str(tmp_path / "wt"),
            "changed_files": [],
            "diff": "",
        },
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/jobs",
            json={
                "prompt": "edit service.py",
                "session_id": "bg-explicit-repo",
                "repo_dir": str(repo_dir),
                "use_worktree": True,
                "worktree_root_dir": str(tmp_path / "worktrees"),
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        final_job = await _poll_job(client, job_id, {"done", "failed"})

    assert final_job is not None
    assert final_job["status"] == "done"
    assert create_calls == [str(repo_dir)]


@pytest.mark.asyncio
async def test_chat_route_does_not_block_health_checks(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    async def fake_run_chat(self, prompt: str, job_id: str | None = None) -> str:
        started.set()
        await asyncio.to_thread(release.wait)
        return "done"

    monkeypatch.setenv("MANTIS_API_KEY", "test-key")
    monkeypatch.setattr("mantis.app.MantisApp._run_chat", fake_run_chat)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={"prompt": "read a file", "session_id": "chat-health"},
            )
        )
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1.0)

        health = await asyncio.wait_for(client.get("/api/health"), timeout=1.0)
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        release.set()
        chat_response = await asyncio.wait_for(chat_task, timeout=1.0)

    assert chat_response.status_code == 200
    assert chat_response.json()["response"] == "done"


@pytest.mark.asyncio
async def test_rerun_failed_workers_creates_job_from_failed_worker_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    jobs_dir = tmp_path / ".mantisai" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_id = "job-failed-workers"
    job_payload = {
        "id": job_id,
        "prompt": "original",
        "session_id": "rerun-session",
        "status": "failed",
        "created_at": "2026-04-05T00:00:00+00:00",
        "updated_at": "2026-04-05T00:00:00+00:00",
        "response": None,
        "error": "worker failed",
        "model": "deepseek-chat",
        "task_type": "feature",
        "subtasks_count": 2,
        "metadata": {
            "execution": {
                "workers": [
                    {
                        "agent_id": "worker-1",
                        "status": "completed",
                        "resume_metadata": {
                            "resume_key": "worker-1",
                            "prompt": "fix a.py",
                            "project_dir": "/tmp/repo",
                            "resumable": True,
                        },
                    },
                    {
                        "agent_id": "worker-2",
                        "status": "failed",
                        "resume_metadata": {
                            "resume_key": "worker-2",
                            "prompt": "fix b.py",
                            "project_dir": "/tmp/repo",
                            "resumable": True,
                        },
                    },
                ]
            }
        },
    }
    (jobs_dir / f"{job_id}.json").write_text(json.dumps(job_payload), encoding="utf-8")

    captured = {}

    async def fake_create_background_job(body):
        captured["prompt"] = body.prompt
        captured["session_id"] = body.session_id
        captured["repo_dir"] = body.repo_dir
        return {"job_id": "rerun-job", "status": "queued"}

    monkeypatch.setattr("mantis.server.create_background_job", fake_create_background_job)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(f"/api/jobs/{job_id}/rerun-failed-workers")

    assert response.status_code == 200
    assert response.json()["job_id"] == "rerun-job"
    assert captured["prompt"] == "fix b.py"
    assert captured["session_id"] == "rerun-session"
    assert captured["repo_dir"] == "/tmp/repo"


@pytest.mark.asyncio
async def test_chat_stream_uses_local_fast_path_for_test_function_queries(tmp_path, monkeypatch):
    test_file = tmp_path / "sample_test_file.py"
    test_file.write_text(
        "def test_alpha():\n    assert True\n\n"
        "def test_beta():\n    assert True\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MANTIS_API_KEY", "test-key")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream(
            "POST",
            "/api/chat/stream",
            json={
                "prompt": f"Read {test_file} and reply only with the names of the test functions.",
                "session_id": "stream-fast-path",
            },
        ) as response:
            assert response.status_code == 200
            chunks = []
            async for text in response.aiter_text():
                chunks.append(text)
            payload = "".join(chunks)

    assert '"execution_mode": "local_fast_path"' in payload
    assert "test_alpha" in payload
    assert "test_beta" in payload


@pytest.mark.asyncio
async def test_chat_stream_uses_local_fast_path_for_simple_return_edit(tmp_path, monkeypatch):
    target = tmp_path / "edit_me.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")

    monkeypatch.setenv("MANTIS_API_KEY", "test-key")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream(
            "POST",
            "/api/chat/stream",
            json={
                "prompt": f"Read {target} and change the return value from 1 to 2.",
                "session_id": "stream-edit-fast-path",
            },
        ) as response:
                assert response.status_code == 200
                payload = "".join([text async for text in response.aiter_text()])

    assert '"execution_mode": "local_fast_path"' in payload
    assert target.read_text(encoding="utf-8") == "def value():\n    return 2\n"
