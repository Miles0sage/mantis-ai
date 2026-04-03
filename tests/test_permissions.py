from __future__ import annotations

from unittest.mock import patch

from mantis.core.approval_store import ApprovalStore
from mantis.core.permissions import PermissionManager
from mantis.core.permissions import PermissionRequiredError


def test_check_denies_high_risk_in_non_interactive_mode():
    manager = PermissionManager(mode="auto")

    with patch("sys.stdin.isatty", return_value=False):
        allowed = manager.check("run_bash", {"command": "pytest -q"})

    assert allowed is False


def test_ask_user_returns_false_on_eof():
    manager = PermissionManager(mode="default")

    with patch("sys.stdin.isatty", return_value=True), patch(
        "builtins.input", side_effect=EOFError
    ):
        allowed = manager.ask_user("run_bash", {"command": "pytest -q"})

    assert allowed is False


def test_check_creates_pending_approval_for_background_job(tmp_path):
    store = ApprovalStore(tmp_path)
    manager = PermissionManager(mode="auto", approval_store=store)
    manager.set_context(session_id="s1", job_id="j1")

    try:
        manager.check("run_bash", {"command": "pytest -q"})
    except PermissionRequiredError as exc:
        pending = store.load(exc.approval_id)
        assert pending is not None
        assert pending.status == "pending"
        assert pending.job_id == "j1"
    else:
        raise AssertionError("expected PermissionRequiredError")


def test_check_reuses_approved_background_permission(tmp_path):
    store = ApprovalStore(tmp_path)
    approval = store.create(
        session_id="s1",
        job_id="j1",
        tool_name="run_bash",
        tool_input={"command": "pytest -q"},
        risk_level="HIGH",
    )
    store.update(approval.id, status="approved")

    manager = PermissionManager(mode="auto", approval_store=store)
    manager.set_context(session_id="s1", job_id="j1")

    assert manager.check("run_bash", {"command": "pytest -q"}) is True
    assert store.load(approval.id).status == "used"


def test_check_pauses_medium_risk_edit_in_background_job(tmp_path):
    file_path = tmp_path / "demo.py"
    file_path.write_text("hello world\n", encoding="utf-8")

    store = ApprovalStore(tmp_path / "approvals")
    manager = PermissionManager(mode="auto", approval_store=store)
    manager.set_context(session_id="s1", job_id="j1")

    try:
        manager.check(
            "edit_file",
            {
                "file_path": str(file_path),
                "old_string": "hello",
                "new_string": "goodbye",
            },
        )
    except PermissionRequiredError as exc:
        pending = store.load(exc.approval_id)
        assert pending is not None
        assert pending.metadata["preview"]["kind"] == "diff"
        assert "goodbye world" in pending.metadata["preview"]["diff"]
    else:
        raise AssertionError("expected PermissionRequiredError")


def test_model_escalation_approval_can_be_reused(tmp_path):
    from mantis.app import MantisApp

    store = ApprovalStore(tmp_path / "approvals")
    original_cls = MantisApp.__init__.__globals__["ApprovalStore"]
    MantisApp.__init__.__globals__["ApprovalStore"] = lambda: store
    try:
        app = MantisApp(
            {
                "openai_api_key": "test-openai",
                "anthropic_api_key": "test-anthropic",
            },
            session_id="s1",
        )
    finally:
        MantisApp.__init__.__globals__["ApprovalStore"] = original_cls

    strong = next(model for model in app.router.list_models() if model.name == "claude-3-5-sonnet")
    routing = {
        "strategy": "auto_plan_router",
        "task_type": "feature",
        "complexity": "high",
        "file_count": 3,
        "task_count": 2,
        "needs_escalation": True,
    }

    try:
        app._maybe_require_model_escalation_approval(
            "refactor a.py and b.py and c.py",
            strong,
            routing,
            job_id="j1",
        )
    except PermissionRequiredError as exc:
        approval = store.load(exc.approval_id)
        assert approval is not None
        assert approval.tool_name == "model_escalation"
        assert approval.metadata["kind"] == "model_escalation"
        assert "claude-3-5-sonnet" in approval.metadata["preview"]["message"]
        store.update(approval.id, status="approved")
    else:
        raise AssertionError("expected PermissionRequiredError")

    app._maybe_require_model_escalation_approval(
        "refactor a.py and b.py and c.py",
        strong,
        routing,
        job_id="j1",
    )
    assert store.list(limit=1)[0].status == "used"
