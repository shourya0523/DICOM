"""Freeze search matches into stable provider-local cohorts."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from provider_gateway.config import Settings, utcnow
from provider_gateway.models import CanonicalSearchQuery, ClinicalEvidenceRecord, Cohort
from provider_gateway.repository import Repository
from provider_gateway.search import query_fingerprint

logger = logging.getLogger("provider_gateway.cohorts")


class CohortService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository

    def freeze(
        self,
        query: CanonicalSearchQuery,
        matches: list[ClinicalEvidenceRecord],
        *,
        index_version: str,
    ) -> Cohort:
        fingerprint = query_fingerprint(query)
        existing = self.repository.get_cohort_by_fingerprint(fingerprint)
        now = utcnow()
        if existing and existing.expires_at > now:
            return existing

        handle = (
            f"cohort-{self.settings.provider_code.lower()}-"
            f"{hashlib.sha256(fingerprint.encode()).hexdigest()[:8]}"
        )
        cohort = Cohort(
            cohort_handle=handle,
            query_id=query.query_id,
            query_fingerprint=fingerprint,
            member_tokens=[m.study_token for m in matches],
            created_at=now,
            expires_at=now + timedelta(hours=self.settings.cohort_ttl_hours),
            index_version=index_version,
        )
        self.repository.save_cohort(cohort)
        logger.info("COHORT_CREATED handle=%s size=%s", handle, len(matches))
        self.repository.log_audit(
            "COHORT_CREATED",
            {
                "cohort_handle": handle,
                "query_id": query.query_id,
                "size": len(matches),
            },
        )
        return cohort

    def get(self, cohort_handle: str) -> Cohort | None:
        return self.repository.get_cohort(cohort_handle)

    def is_valid(self, cohort: Cohort) -> bool:
        return cohort.expires_at > utcnow()
