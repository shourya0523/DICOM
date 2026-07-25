"""Organisation policy checks and access-request decisions."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from provider_gateway.config import ALLOWED_DATA_FIELDS, ALLOWED_METADATA_FIELDS, utcnow
from provider_gateway.models import (
    AccessRequest,
    AccessRequestCreate,
    AccessRequestEvent,
    AccessRequestStatus,
    Cohort,
    OrganisationPolicy,
    OrgStatus,
)
from provider_gateway.repository import Repository

logger = logging.getLogger("provider_gateway.approvals")


class ApprovalService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def get_org(self, organisation_id: str) -> OrganisationPolicy | None:
        return self.repository.get_organisation(organisation_id)

    def decide(
        self,
        request: AccessRequestCreate,
        cohort: Cohort | None,
    ) -> tuple[AccessRequestStatus, str, str | None, list[str]]:
        """Return status, reason, approval_path, approved_fields."""
        if not request.project_title.strip() or not request.purpose.strip():
            return (
                AccessRequestStatus.REJECTED,
                "Project title and purpose are required",
                "policy",
                [],
            )

        if cohort is None:
            return (
                AccessRequestStatus.REJECTED,
                "Cohort not found",
                "policy",
                [],
            )

        if cohort.expires_at <= utcnow():
            return (
                AccessRequestStatus.EXPIRED,
                "Cohort expired",
                "policy",
                [],
            )

        global_meta = set(ALLOWED_METADATA_FIELDS)
        global_data = set(ALLOWED_DATA_FIELDS)
        req_meta = set(request.requested_metadata_fields)
        req_data = set(request.requested_data_fields)

        if not req_meta.issubset(global_meta) or not req_data.issubset(global_data):
            return (
                AccessRequestStatus.REJECTED,
                "Requested fields include prohibited values",
                "policy",
                [],
            )

        org = self.get_org(request.organisation_id)
        if org is None or org.status != OrgStatus.ACTIVE:
            return (
                AccessRequestStatus.PENDING_REVIEW,
                "Organisation unknown or not active",
                "manual_review",
                [],
            )

        if not self._policy_currently_valid(org):
            return (
                AccessRequestStatus.PENDING_REVIEW,
                "Organisation policy not currently valid",
                "manual_review",
                [],
            )

        if req_meta and not org.metadata_auto_approval:
            return (
                AccessRequestStatus.PENDING_REVIEW,
                "Metadata auto-approval disabled",
                "manual_review",
                [],
            )
        if req_data and not org.data_auto_approval:
            return (
                AccessRequestStatus.PENDING_REVIEW,
                "Data auto-approval disabled",
                "manual_review",
                [],
            )

        if not req_meta.issubset(set(org.allowed_metadata_fields)):
            return (
                AccessRequestStatus.REJECTED,
                "Requested metadata fields not permitted for organisation",
                "policy",
                [],
            )
        if not req_data.issubset(set(org.allowed_data_fields)):
            return (
                AccessRequestStatus.REJECTED,
                "Requested data fields not permitted for organisation",
                "policy",
                [],
            )

        approved = sorted(req_meta | req_data)
        return (
            AccessRequestStatus.APPROVED,
            "Auto-approved under organisation policy",
            "auto",
            approved,
        )

    def _policy_currently_valid(self, org: OrganisationPolicy) -> bool:
        now = utcnow()
        if org.valid_from and org.valid_from > now:
            return False
        if org.valid_to and org.valid_to < now:
            return False
        return True

    def transition(
        self,
        request: AccessRequest,
        to_status: AccessRequestStatus,
        reason: str,
        *,
        actor: str = "gateway",
    ) -> AccessRequest:
        event = AccessRequestEvent(
            timestamp=utcnow(),
            from_status=request.status.value,
            to_status=to_status.value,
            reason=reason,
            actor=actor,
        )
        request.status = to_status
        request.updated_at = event.timestamp
        request.decision_reason = reason
        request.events.append(event)
        self.repository.save_access_request(request)
        self.repository.append_access_event(request.provider_request_id, event)
        return request

    def manual_approve(
        self,
        request: AccessRequest,
        *,
        reason: str | None = None,
        approved_fields: list[str] | None = None,
        actor: str = "hospital_admin",
    ) -> AccessRequest:
        if request.status != AccessRequestStatus.PENDING_REVIEW:
            raise ValueError(
                f"Only PENDING_REVIEW requests can be approved (got {request.status.value})"
            )
        fields = approved_fields
        if fields is None:
            fields = sorted(
                set(request.requested_metadata_fields)
                | set(request.requested_data_fields)
            )
        request.approval_path = "manual"
        request.approved_fields = list(fields)
        return self.transition(
            request,
            AccessRequestStatus.APPROVED,
            reason or "Manually approved by hospital administrator",
            actor=actor,
        )

    def manual_deny(
        self,
        request: AccessRequest,
        *,
        reason: str | None = None,
        actor: str = "hospital_admin",
    ) -> AccessRequest:
        if request.status != AccessRequestStatus.PENDING_REVIEW:
            raise ValueError(
                f"Only PENDING_REVIEW requests can be denied (got {request.status.value})"
            )
        request.approval_path = "manual"
        request.approved_fields = []
        return self.transition(
            request,
            AccessRequestStatus.REJECTED,
            reason or "Denied by hospital administrator",
            actor=actor,
        )

    def new_request(self, create: AccessRequestCreate) -> AccessRequest:
        now = utcnow()
        request = AccessRequest(
            provider_request_id=f"par-{uuid.uuid4().hex[:12]}",
            coordinator_access_request_id=create.coordinator_access_request_id,
            organisation_id=create.organisation_id,
            researcher_id=create.researcher_id,
            cohort_handle=create.cohort_handle,
            project_title=create.project_title,
            purpose=create.purpose,
            requested_metadata_fields=list(create.requested_metadata_fields),
            requested_data_fields=list(create.requested_data_fields),
            approved_fields=[],
            status=AccessRequestStatus.RECEIVED,
            created_at=now,
            updated_at=now,
            events=[],
        )
        self.repository.save_access_request(request)
        self.repository.append_access_event(
            request.provider_request_id,
            AccessRequestEvent(
                timestamp=now,
                from_status=None,
                to_status=AccessRequestStatus.RECEIVED.value,
                reason="Access request received",
            ),
        )
        logger.info(
            "ACCESS_REQUEST_RECEIVED id=%s org=%s",
            request.provider_request_id,
            request.organisation_id,
        )
        return request
