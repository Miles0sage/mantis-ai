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
class ApprovalRecord:
    id: str
    session_id: str
    job_id: str | None
    tool_name: str
    tool_input: dict[str, Any]
    risk_level: str
    status: str
    created_at: str
    updated_at: str
    decision_note: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApprovalStore:
    def __init__(self, approvals_dir: str | Path | None = None) -> None:
        self.approvals_dir = Path(approvals_dir or (Path.home() / ".mantisai" / "approvals"))
        self.approvals_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        risk_level: str,
        job_id: str | None = None,
        **extra: Any,
    ) -> ApprovalRecord:
        now = utc_now_iso()
        approval = ApprovalRecord(
            id=str(uuid4()),
            session_id=session_id,
            job_id=job_id,
            tool_name=tool_name,
            tool_input=tool_input,
            risk_level=risk_level,
            status="pending",
            created_at=now,
            updated_at=now,
            metadata=extra,
        )
        self.save(approval)
        return approval

    def save(self, approval: ApprovalRecord) -> None:
        self.path_for(approval.id).write_text(
            json.dumps(approval.to_dict(), indent=2),
            encoding="utf-8",
        )

    def load(self, approval_id: str) -> ApprovalRecord | None:
        path = self.path_for(approval_id)
        if not path.exists():
            return None
        return ApprovalRecord(**json.loads(path.read_text(encoding="utf-8")))

    def update(self, approval_id: str, **fields: Any) -> ApprovalRecord | None:
        approval = self.load(approval_id)
        if approval is None:
            return None
        for key, value in fields.items():
            setattr(approval, key, value)
        approval.updated_at = utc_now_iso()
        self.save(approval)
        return approval

    def list(self, limit: int = 50, status: str | None = None) -> list[ApprovalRecord]:
        approvals: list[ApprovalRecord] = []
        for path in sorted(self.approvals_dir.glob("*.json"), reverse=True):
            try:
                approval = ApprovalRecord(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if status and approval.status != status:
                continue
            approvals.append(approval)
        approvals.sort(key=lambda a: a.updated_at, reverse=True)
        return approvals[:limit]

    def find_approved(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        job_id: str | None = None,
    ) -> ApprovalRecord | None:
        return self._find_by_status(
            status="approved",
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            job_id=job_id,
        )

    def find_pending(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        job_id: str | None = None,
    ) -> ApprovalRecord | None:
        return self._find_by_status(
            status="pending",
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            job_id=job_id,
        )

    def _find_by_status(
        self,
        status: str,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        job_id: str | None = None,
    ) -> ApprovalRecord | None:
        for approval in self.list(limit=200):
            if approval.status != status:
                continue
            if approval.session_id != session_id:
                continue
            if approval.tool_name != tool_name:
                continue
            if approval.tool_input != tool_input:
                continue
            if job_id is not None and approval.job_id not in {None, job_id}:
                continue
            return approval
        return None

    def path_for(self, approval_id: str) -> Path:
        return self.approvals_dir / f"{approval_id}.json"
