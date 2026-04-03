from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ActivityEvent:
    id: str
    session_id: str
    event_type: str
    message: str
    created_at: str
    job_id: str | None = None
    approval_id: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ActivityStore:
    def __init__(self, events_dir: str | Path | None = None) -> None:
        self.events_dir = Path(events_dir or (Path.home() / ".mantisai" / "activity"))
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        session_id: str,
        event_type: str,
        message: str,
        job_id: str | None = None,
        approval_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActivityEvent:
        event = ActivityEvent(
            id=str(uuid4()),
            session_id=session_id,
            event_type=event_type,
            message=message,
            created_at=utc_now_iso(),
            job_id=job_id,
            approval_id=approval_id,
            metadata=metadata or {},
        )
        self.path_for(event.id).write_text(
            json.dumps(event.to_dict(), indent=2),
            encoding="utf-8",
        )
        return event

    def list(self, session_id: str | None = None, limit: int = 50) -> list[ActivityEvent]:
        events: list[ActivityEvent] = []
        for path in sorted(self.events_dir.glob("*.json"), reverse=True):
            try:
                event = ActivityEvent(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if session_id and event.session_id != session_id:
                continue
            events.append(event)
        events.sort(key=lambda e: e.created_at, reverse=True)
        return events[:limit]

    def path_for(self, event_id: str) -> Path:
        return self.events_dir / f"{event_id}.json"
