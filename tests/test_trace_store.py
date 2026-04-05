from mantis.core.trace_store import TraceStore


def test_trace_store_create_and_list(tmp_path):
    store = TraceStore(tmp_path)

    first = store.create(
        session_id="s1",
        prompt="fix auth bug",
        response="done",
        model="gpt-4o-mini",
        provider="openai-compatible",
        job_id="job-1",
        stats={"cost": 0.12, "execution": {"task_count": 1}},
    )
    second = store.create(
        session_id="s2",
        prompt="write tests",
        response="ok",
    )

    loaded = store.load(first.id)
    assert loaded is not None
    assert loaded.prompt == "fix auth bug"
    assert loaded.stats["execution"]["task_count"] == 1

    traces = store.list(limit=10)
    assert len(traces) == 2
    session_filtered = store.list(session_id="s1", limit=10)
    assert len(session_filtered) == 1
    assert session_filtered[0].id == first.id
    assert any(trace.id == second.id for trace in traces)
