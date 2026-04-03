from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SessionRecord:
    session_id: str
    history: list[dict[str, Any]] = field(default_factory=list)
    last_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionStore:
    def __init__(self, sessions_dir: str | Path | None = None) -> None:
        self.sessions_dir = Path(sessions_dir or (Path.home() / ".mantisai" / "sessions"))
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def load(self, session_id: str) -> SessionRecord:
        path = self.path_for(session_id)
        if not path.exists():
            return SessionRecord(session_id=session_id)
        return SessionRecord(**json.loads(path.read_text(encoding="utf-8")))

    def save(self, session: SessionRecord) -> None:
        self.path_for(session.session_id).write_text(
            json.dumps(session.to_dict(), indent=2),
            encoding="utf-8",
        )

    def append(
        self,
        session_id: str,
        entry: dict[str, Any],
        last_stats: dict[str, Any] | None = None,
    ) -> SessionRecord:
        session = self.load(session_id)
        session.history.append(entry)
        if last_stats is not None:
            session.last_stats = last_stats
        self.save(session)
        return session

    def path_for(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"
