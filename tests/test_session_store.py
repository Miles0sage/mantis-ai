from __future__ import annotations

from mantis.core.session_store import SessionStore


def test_load_missing_session_returns_empty_record(tmp_path):
    store = SessionStore(tmp_path)
    session = store.load("abc")
    assert session.session_id == "abc"
    assert session.history == []


def test_append_persists_history_and_stats(tmp_path):
    store = SessionStore(tmp_path)
    store.append(
        "s1",
        {"prompt": "hello", "response": "world"},
        last_stats={"model": "deepseek-chat"},
    )

    session = store.load("s1")
    assert len(session.history) == 1
    assert session.history[0]["prompt"] == "hello"
    assert session.last_stats["model"] == "deepseek-chat"
