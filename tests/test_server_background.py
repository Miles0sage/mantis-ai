from __future__ import annotations

import time

from fastapi.testclient import TestClient

from mantis.server import app


def test_background_job_completes_with_testclient(tmp_path, monkeypatch):
    target = tmp_path / "edit_me.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")

    async def fake_run_chat(self, prompt: str, job_id: str | None = None) -> str:
        target.write_text("def value():\n    return 2\n", encoding="utf-8")
        return "updated"

    monkeypatch.setenv("MANTIS_API_KEY", "test-key")
    monkeypatch.setattr("mantis.app.MantisApp._run_chat", fake_run_chat)

    client = TestClient(app)
    response = client.post(
        "/api/jobs",
        json={"prompt": f"edit {target}", "session_id": "bg-test"},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    final_job = None
    for _ in range(20):
        time.sleep(0.1)
        final_job = client.get(f"/api/jobs/{job_id}").json()
        if final_job["status"] in {"done", "failed"}:
            break

    assert final_job is not None
    assert final_job["status"] == "done"
    assert target.read_text(encoding="utf-8") == "def value():\n    return 2\n"


def test_background_job_waits_for_model_escalation_approval(tmp_path, monkeypatch):
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

    client = TestClient(app)
    response = client.post(
        "/api/jobs",
        json={"prompt": "refactor a.py and b.py and c.py", "session_id": "bg-model-approval"},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    waiting_job = None
    for _ in range(20):
        time.sleep(0.1)
        waiting_job = client.get(f"/api/jobs/{job_id}").json()
        if waiting_job["status"] == "awaiting_approval":
            break

    assert waiting_job is not None
    assert waiting_job["status"] == "awaiting_approval"
    assert waiting_job["metadata"]["tool_name"] == "model_escalation"
    approval_id = waiting_job["metadata"]["approval_id"]

    approve = client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"note": "allow stronger model"},
    )
    assert approve.status_code == 200

    final_job = None
    for _ in range(20):
        time.sleep(0.1)
        final_job = client.get(f"/api/jobs/{job_id}").json()
        if final_job["status"] in {"done", "failed"}:
            break

    assert final_job is not None
    assert final_job["status"] == "done"
    assert final_job["response"] == "escalated result"
