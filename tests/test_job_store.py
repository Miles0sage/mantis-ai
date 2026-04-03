from __future__ import annotations

from mantis.core.job_store import JobStore


def test_create_and_load_job(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("fix auth bug", "session-a", model="deepseek-chat")

    loaded = store.load(job.id)
    assert loaded is not None
    assert loaded.prompt == "fix auth bug"
    assert loaded.session_id == "session-a"
    assert loaded.status == "queued"
    assert loaded.model == "deepseek-chat"


def test_update_job_fields(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("write tests", "session-b")

    updated = store.update(job.id, status="done", response="ok")
    assert updated is not None
    assert updated.status == "done"
    assert updated.response == "ok"
    assert updated.updated_at >= updated.created_at


def test_list_orders_most_recent_first(tmp_path):
    store = JobStore(tmp_path)
    older = store.create("first", "s1")
    newer = store.create("second", "s1")
    store.update(older.id, status="running")

    jobs = store.list()
    assert len(jobs) == 2
    assert jobs[0].id == older.id
