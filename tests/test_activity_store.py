from __future__ import annotations

from mantis.core.activity_store import ActivityStore


def test_create_and_list_activity_events(tmp_path):
    store = ActivityStore(tmp_path)
    first = store.create(session_id="s1", event_type="job_queued", message="Queued job")
    second = store.create(session_id="s1", event_type="job_done", message="Done job")

    events = store.list(session_id="s1")
    assert len(events) == 2
    assert events[0].id == second.id
    assert events[1].id == first.id


def test_list_activity_filters_session(tmp_path):
    store = ActivityStore(tmp_path)
    store.create(session_id="s1", event_type="job_queued", message="s1")
    store.create(session_id="s2", event_type="job_queued", message="s2")

    events = store.list(session_id="s1")
    assert len(events) == 1
    assert events[0].session_id == "s1"
