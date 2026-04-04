"""Tests for VIGIL — the MantisAI background watchdog daemon."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from mantis.core.job_store import JobStore
from mantis.core.vigil import Vigil


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def _fixed_now(iso: str):
    dt = _dt(iso)
    return lambda: dt


# ---------------------------------------------------------------------------
# Stall detection
# ---------------------------------------------------------------------------

def test_requeue_stalled_job(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = store.create("fix bug", "s1")
    store.update(job.id, status="running")
    # Manually backdate updated_at so the job looks stalled
    store.update(job.id, updated_at="2026-01-01T00:00:00+00:00")

    now = _dt("2026-01-01T00:15:00+00:00")  # 15 minutes later
    vigil = Vigil(store, stall_timeout_minutes=10, _now_fn=lambda: now)

    requeued = vigil._requeue_stalled(now)

    assert job.id in requeued
    reloaded = store.load(job.id)
    assert reloaded.status == "queued"
    assert reloaded.metadata["vigil_requeue_count"] == 1


def test_does_not_requeue_fresh_running_job(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = store.create("write tests", "s1")
    store.update(job.id, status="running")
    # updated_at is "now" so not stalled
    reloaded = store.load(job.id)
    now = _dt(reloaded.updated_at[:26] + "+00:00" if "+" not in reloaded.updated_at else reloaded.updated_at)
    vigil = Vigil(store, stall_timeout_minutes=10, _now_fn=lambda: now)

    requeued = vigil._requeue_stalled(now)
    assert job.id not in requeued
    assert store.load(job.id).status == "running"


def test_does_not_requeue_non_running_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs")
    for status in ("queued", "done", "failed"):
        j = store.create(f"task {status}", "s1")
        store.update(j.id, status=status,
                     updated_at="2026-01-01T00:00:00+00:00")

    now = _dt("2026-01-01T01:00:00+00:00")
    vigil = Vigil(store, stall_timeout_minutes=10, _now_fn=lambda: now)
    requeued = vigil._requeue_stalled(now)
    assert requeued == []


def test_requeue_increments_count(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = store.create("task", "s1")
    store.update(job.id, status="running",
                 updated_at="2026-01-01T00:00:00+00:00",
                 metadata={"vigil_requeue_count": 2})

    now = _dt("2026-01-01T00:20:00+00:00")
    vigil = Vigil(store, stall_timeout_minutes=10, _now_fn=lambda: now)
    vigil._requeue_stalled(now)

    assert store.load(job.id).metadata["vigil_requeue_count"] == 3


# ---------------------------------------------------------------------------
# Nightly evolution
# ---------------------------------------------------------------------------

def test_evolve_writes_reflection_json(tmp_path):
    store = JobStore(tmp_path / "jobs")
    vigil_dir = tmp_path / "vigil"

    # Add some completed jobs
    for i in range(3):
        j = store.create(f"fix bug {i}", "s1", task_type="bug_fix")
        store.update(j.id, status="done",
                     created_at="2026-01-01T02:00:00+00:00",
                     updated_at="2026-01-01T02:05:00+00:00")

    j_fail = store.create("failed task", "s1", task_type="bug_fix")
    store.update(j_fail.id, status="failed",
                 created_at="2026-01-01T02:00:00+00:00",
                 updated_at="2026-01-01T02:03:00+00:00")

    now = _dt("2026-01-01T02:00:00+00:00")
    vigil = Vigil(store, vigil_dir=vigil_dir, _now_fn=lambda: now)
    result = vigil._evolve(now)

    out_file = vigil_dir / "2026-01-01.json"
    assert out_file.exists()

    data = json.loads(out_file.read_text())
    assert data["date"] == "2026-01-01"
    assert data["jobs_analysed"] == 4
    bug_fix = data["by_task_type"]["bug_fix"]
    assert bug_fix["total"] == 4
    assert bug_fix["success_rate"] == 0.75


def test_evolve_unknown_task_type(tmp_path):
    store = JobStore(tmp_path / "jobs")
    vigil_dir = tmp_path / "vigil"

    j = store.create("mystery task", "s1")  # task_type=None
    store.update(j.id, status="done")

    now = _dt("2026-01-01T02:00:00+00:00")
    vigil = Vigil(store, vigil_dir=vigil_dir, _now_fn=lambda: now)
    result = vigil._evolve(now)

    assert "unknown" in result["by_task_type"]


def test_evolve_empty_store(tmp_path):
    store = JobStore(tmp_path / "jobs")
    vigil_dir = tmp_path / "vigil"
    now = _dt("2026-01-01T02:00:00+00:00")
    vigil = Vigil(store, vigil_dir=vigil_dir, _now_fn=lambda: now)
    result = vigil._evolve(now)

    assert result["jobs_analysed"] == 0
    assert result["by_task_type"] == {}


# ---------------------------------------------------------------------------
# Tick gating — evolution only runs once per day at the right hour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_triggers_evolve_at_correct_hour(tmp_path):
    store = JobStore(tmp_path / "jobs")
    vigil_dir = tmp_path / "vigil"

    now = _dt("2026-01-01T02:30:00+00:00")  # evolve_hour=2 → should fire
    vigil = Vigil(store, vigil_dir=vigil_dir, evolve_hour=2, _now_fn=lambda: now)

    await vigil._tick()

    assert (vigil_dir / "2026-01-01.json").exists()


@pytest.mark.asyncio
async def test_tick_does_not_evolve_twice_same_day(tmp_path):
    store = JobStore(tmp_path / "jobs")
    vigil_dir = tmp_path / "vigil"

    now = _dt("2026-01-01T02:30:00+00:00")
    vigil = Vigil(store, vigil_dir=vigil_dir, evolve_hour=2, _now_fn=lambda: now)

    await vigil._tick()
    out_file = vigil_dir / "2026-01-01.json"
    first_mtime = out_file.stat().st_mtime

    await vigil._tick()  # second tick — same day, should not overwrite
    assert out_file.stat().st_mtime == first_mtime


@pytest.mark.asyncio
async def test_tick_does_not_evolve_wrong_hour(tmp_path):
    store = JobStore(tmp_path / "jobs")
    vigil_dir = tmp_path / "vigil"

    now = _dt("2026-01-01T15:00:00+00:00")  # evolve_hour=2, but it's 15h
    vigil = Vigil(store, vigil_dir=vigil_dir, evolve_hour=2, _now_fn=lambda: now)

    await vigil._tick()
    assert not (vigil_dir / "2026-01-01.json").exists()


# ---------------------------------------------------------------------------
# Stop / run lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_terminates_run(tmp_path):
    store = JobStore(tmp_path / "jobs")
    vigil = Vigil(store, tick_interval=1, _now_fn=_utcnow_fn())
    task = asyncio.create_task(vigil.run())
    await asyncio.sleep(0.1)
    vigil.stop()
    await asyncio.wait_for(task, timeout=3.0)
    assert task.done()


def _utcnow_fn():
    from datetime import datetime, timezone
    return lambda: datetime.now(timezone.utc)
