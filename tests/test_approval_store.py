from __future__ import annotations

from mantis.core.approval_store import ApprovalStore


def test_create_and_load_approval(tmp_path):
    store = ApprovalStore(tmp_path)
    approval = store.create(
        session_id="s1",
        job_id="j1",
        tool_name="run_bash",
        tool_input={"command": "pytest -q"},
        risk_level="HIGH",
    )

    loaded = store.load(approval.id)
    assert loaded is not None
    assert loaded.tool_name == "run_bash"
    assert loaded.status == "pending"


def test_find_pending_and_approved(tmp_path):
    store = ApprovalStore(tmp_path)
    approval = store.create(
        session_id="s1",
        job_id="j1",
        tool_name="run_bash",
        tool_input={"command": "pytest -q"},
        risk_level="HIGH",
    )

    pending = store.find_pending("s1", "run_bash", {"command": "pytest -q"}, job_id="j1")
    assert pending is not None
    assert pending.id == approval.id

    store.update(approval.id, status="approved")
    approved = store.find_approved("s1", "run_bash", {"command": "pytest -q"}, job_id="j1")
    assert approved is not None
    assert approved.id == approval.id
