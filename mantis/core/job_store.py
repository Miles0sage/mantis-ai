from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobRecord:
    id: str
    prompt: str
    session_id: str
    status: str
    created_at: str
    updated_at: str
    response: str | None = None
    error: str | None = None
    model: str | None = None
    task_type: str | None = None
    subtasks_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobStore:
    def __init__(self, jobs_dir: str | Path | None = None) -> None:
        self.jobs_dir = Path(jobs_dir or (Path.home() / ".mantisai" / "jobs"))
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def create(self, prompt: str, session_id: str, **extra: Any) -> JobRecord:
        now = utc_now_iso()
        job = JobRecord(
            id=str(uuid4()),
            prompt=prompt,
            session_id=session_id,
            status="queued",
            created_at=now,
            updated_at=now,
            model=extra.pop("model", None),
            task_type=extra.pop("task_type", None),
            subtasks_count=extra.pop("subtasks_count", None),
            metadata=extra,
        )
        self.save(job)
        return job

    def save(self, job: JobRecord) -> None:
        self.path_for(job.id).write_text(json.dumps(job.to_dict(), indent=2), encoding="utf-8")

    def load(self, job_id: str) -> JobRecord | None:
        path = self.path_for(job_id)
        if not path.exists():
            return None
        return JobRecord(**json.loads(path.read_text(encoding="utf-8")))

    def update(self, job_id: str, **fields: Any) -> JobRecord | None:
        job = self.load(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            setattr(job, key, value)
        if "updated_at" not in fields:
            job.updated_at = utc_now_iso()
        self.save(job)
        return job

    def list(self, limit: int = 50) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for path in sorted(self.jobs_dir.glob("*.json"), reverse=True):
            try:
                jobs.append(JobRecord(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        jobs.sort(key=lambda j: j.updated_at, reverse=True)
        return jobs[:limit]

    def path_for(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"
