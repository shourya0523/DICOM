"""In-memory audit log for continuous monitoring demo."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from shared.contracts import AuditAction, AuditDecision, AuditEvent

_audit_log: list[AuditEvent] = []


def record(
    *,
    researcher_id: str,
    action: AuditAction,
    decision: AuditDecision,
    reason: str,
    detail: Optional[str] = None,
) -> AuditEvent:
    event = AuditEvent(
        ts=datetime.now(timezone.utc).isoformat(),
        researcher_id=researcher_id,
        action=action,
        decision=decision,
        reason=reason,
        detail=detail,
    )
    _audit_log.append(event)
    return event


def list_events() -> list[AuditEvent]:
    return list(_audit_log)


def clear() -> None:
    _audit_log.clear()
