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
class TraceRecord:
    id: str
    created_at: str
    session_id: str
    prompt: str
    response: str
    model: str | None = None
    provider: str | None = None
    job_id: str | None = None
    approval_id: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TraceStore:
    def __init__(self, traces_dir: str | Path | None = None) -> None:
        self.traces_dir = Path(traces_dir or (Path.home() / ".mantisai" / "traces"))
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        session_id: str,
        prompt: str,
        response: str,
        model: str | None = None,
        provider: str | None = None,
        job_id: str | None = None,
        approval_id: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> TraceRecord:
        trace = TraceRecord(
            id=str(uuid4()),
            created_at=utc_now_iso(),
            session_id=session_id,
            prompt=prompt,
            response=response,
            model=model,
            provider=provider,
            job_id=job_id,
            approval_id=approval_id,
            stats=stats or {},
        )
        self.path_for(trace.id).write_text(
            json.dumps(trace.to_dict(), indent=2),
            encoding="utf-8",
        )
        return trace

    def load(self, trace_id: str) -> TraceRecord | None:
        path = self.path_for(trace_id)
        if not path.exists():
            return None
        return TraceRecord(**json.loads(path.read_text(encoding="utf-8")))

    def list(self, session_id: str | None = None, limit: int = 50) -> list[TraceRecord]:
        traces: list[TraceRecord] = []
        for path in sorted(self.traces_dir.glob("*.json"), reverse=True):
            try:
                trace = TraceRecord(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if session_id and trace.session_id != session_id:
                continue
            traces.append(trace)
        traces.sort(key=lambda trace: trace.created_at, reverse=True)
        return traces[:limit]

    def path_for(self, trace_id: str) -> Path:
        return self.traces_dir / f"{trace_id}.json"
