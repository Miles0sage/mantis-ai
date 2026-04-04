"""
VIGIL — background watchdog daemon for MantisAI.

Ticks every `tick_interval` seconds (default 60).

Each tick:
  - Scans jobs stuck in "running" longer than `stall_timeout_minutes` → requeues them.

Nightly (at `evolve_hour` UTC, default 2):
  - Reads the last 200 completed/failed jobs.
  - Computes success rates and average durations by task_type.
  - Writes a reflection JSON to `vigil_dir / YYYY-MM-DD.json`.

Usage (embed in app.py or run standalone)::

    vigil = Vigil(job_store)
    asyncio.create_task(vigil.run())
    ...
    vigil.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from mantis.core.job_store import JobStore

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_to_dt(iso: str) -> datetime:
    """Parse ISO-8601 string; return UTC datetime."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Vigil:
    """Background watchdog for MantisAI jobs.

    Args:
        job_store: JobStore instance to monitor.
        tick_interval: Seconds between ticks (default 60).
        stall_timeout_minutes: Minutes a job may stay in "running"
            before being considered stalled (default 10).
        evolve_hour: UTC hour at which the nightly evolution runs
            (default 2, i.e. 02:xx UTC).
        vigil_dir: Directory where nightly reflection JSONs are written
            (default ~/.mantisai/vigil/).
        _now_fn: Injectable clock for testing.
    """

    def __init__(
        self,
        job_store: JobStore,
        tick_interval: int = 60,
        stall_timeout_minutes: int = 10,
        evolve_hour: int = 2,
        vigil_dir: Optional[Path] = None,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.job_store = job_store
        self.tick_interval = tick_interval
        self.stall_timeout_minutes = stall_timeout_minutes
        self.evolve_hour = evolve_hour
        self.vigil_dir = Path(vigil_dir or (Path.home() / ".mantisai" / "vigil"))
        self.vigil_dir.mkdir(parents=True, exist_ok=True)
        self._now_fn: Callable[[], datetime] = _now_fn or _utcnow
        self._stop_event = asyncio.Event()
        self._last_evolve_date: Optional[str] = None  # "YYYY-MM-DD"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the daemon until stop() is called."""
        logger.info("VIGIL started (tick=%ss, stall=%sm, evolve_hour=%s UTC)",
                    self.tick_interval, self.stall_timeout_minutes, self.evolve_hour)
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("VIGIL tick error: %s", exc)
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=self.tick_interval,
                )
            except asyncio.TimeoutError:
                pass  # normal — time to tick again

    def stop(self) -> None:
        """Signal the daemon to shut down after the current tick."""
        logger.info("VIGIL stopping.")
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        now = self._now_fn()
        requeued = self._requeue_stalled(now)
        if requeued:
            logger.info("VIGIL requeued %d stalled job(s).", len(requeued))

        today = now.strftime("%Y-%m-%d")
        if now.hour == self.evolve_hour and self._last_evolve_date != today:
            self._evolve(now)
            self._last_evolve_date = today

    def _requeue_stalled(self, now: datetime) -> list[str]:
        """Find jobs stuck in 'running' too long and reset them to 'queued'."""
        requeued: list[str] = []
        for job in self.job_store.list(limit=200):
            if job.status != "running":
                continue
            updated_at = _iso_to_dt(job.updated_at)
            elapsed_minutes = (now - updated_at).total_seconds() / 60
            if elapsed_minutes >= self.stall_timeout_minutes:
                stall_note = (
                    f"VIGIL: requeued after {elapsed_minutes:.1f}m stall "
                    f"at {now.isoformat()}"
                )
                meta = dict(job.metadata)
                meta.setdefault("vigil_requeue_count", 0)
                meta["vigil_requeue_count"] = meta["vigil_requeue_count"] + 1
                meta["vigil_last_requeue"] = now.isoformat()
                self.job_store.update(
                    job.id,
                    status="queued",
                    error=stall_note,
                    metadata=meta,
                )
                requeued.append(job.id)
                logger.warning("VIGIL: job %s stalled for %.1fm — requeued.", job.id, elapsed_minutes)
        return requeued

    def _evolve(self, now: datetime) -> dict:
        """Analyse recent job history and write a nightly reflection JSON."""
        jobs = self.job_store.list(limit=200)
        completed = [j for j in jobs if j.status in {"done", "failed", "error"}]

        by_type: dict[str, dict] = {}
        for job in completed:
            t = job.task_type or "unknown"
            bucket = by_type.setdefault(t, {"total": 0, "succeeded": 0, "failed": 0, "durations_s": []})
            bucket["total"] += 1
            if job.status == "done":
                bucket["succeeded"] += 1
            else:
                bucket["failed"] += 1
            try:
                created = _iso_to_dt(job.created_at)
                updated = _iso_to_dt(job.updated_at)
                duration = (updated - created).total_seconds()
                if duration >= 0:
                    bucket["durations_s"].append(duration)
            except (ValueError, KeyError):
                pass

        summary: dict = {}
        for t, b in by_type.items():
            total = b["total"]
            durations = b["durations_s"]
            summary[t] = {
                "total": total,
                "success_rate": round(b["succeeded"] / total, 3) if total else 0,
                "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
            }

        reflection = {
            "date": now.strftime("%Y-%m-%d"),
            "generated_at": now.isoformat(),
            "jobs_analysed": len(completed),
            "by_task_type": summary,
        }

        out_path = self.vigil_dir / f"{now.strftime('%Y-%m-%d')}.json"
        out_path.write_text(json.dumps(reflection, indent=2), encoding="utf-8")
        logger.info("VIGIL nightly reflection written to %s", out_path)
        return reflection
