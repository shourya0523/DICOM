"""Local canonical search over Clinical Evidence Records."""

from __future__ import annotations

import hashlib
import json
import logging

from provider_gateway.config import count_band
from provider_gateway.models import (
    CanonicalSearchQuery,
    ClinicalEvidenceRecord,
    SearchAggregateResponse,
    SearchFilters,
)
from provider_gateway.redaction import normalise_body_parts, normalise_modalities
from provider_gateway.repository import Repository

logger = logging.getLogger("provider_gateway.search")


def normalise_filters(filters: SearchFilters) -> SearchFilters:
    """Deduplicate and sort list filters for matching and fingerprinting."""
    concepts = sorted(
        filters.concepts,
        key=lambda c: (c.code, c.assertion.value),
    )
    return filters.model_copy(
        update={
            "modalities": normalise_modalities(filters.modalities),
            "body_parts": normalise_body_parts(filters.body_parts),
            "concepts": concepts,
        }
    )


def query_fingerprint(query: CanonicalSearchQuery) -> str:
    normalised = query.model_copy(
        update={"filters": normalise_filters(query.filters)}
    )
    payload = normalised.model_dump(mode="json")
    payload.pop("freeze_cohort", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def matches_filters(record: ClinicalEvidenceRecord, query: CanonicalSearchQuery) -> bool:
    filters = normalise_filters(query.filters)
    if filters.age_min is not None and record.age_years < filters.age_min:
        return False
    if filters.age_max is not None and record.age_years > filters.age_max:
        return False
    if filters.modalities and not (
        set(filters.modalities) & set(record.modalities)
    ):
        return False
    if filters.body_parts and not (
        set(filters.body_parts) & set(record.body_parts)
    ):
        return False
    if filters.sex and record.sex.upper() != filters.sex.upper():
        return False
    for concept_filter in filters.concepts:
        found = False
        for concept in record.concepts:
            if (
                concept.code == concept_filter.code
                and concept.assertion == concept_filter.assertion
            ):
                found = True
                break
        if not found:
            return False
    return True


def search_records(
    records: list[ClinicalEvidenceRecord], query: CanonicalSearchQuery
) -> list[ClinicalEvidenceRecord]:
    return [r for r in records if matches_filters(r, query)]


def aggregate(
    provider: str,
    query: CanonicalSearchQuery,
    matches: list[ClinicalEvidenceRecord],
    *,
    index_timestamp,
    access_available: bool = True,
    cohort_handle: str | None = None,
) -> SearchAggregateResponse:
    matched_modalities = sorted(
        {modality for record in matches for modality in record.modalities}
    )
    matched_body_parts = sorted(
        {body_part for record in matches for body_part in record.body_parts}
    )
    n = len(matches)
    response = SearchAggregateResponse(
        provider=provider,
        query_id=query.query_id,
        match_count=n,
        count_band=count_band(n),
        modalities=matched_modalities,
        body_parts=matched_body_parts,
        index_timestamp=index_timestamp,
        access_available=access_available,
        cohort_handle=cohort_handle,
    )
    logger.info(
        "SEARCH_EXECUTED query_id=%s match_count=%s band=%s",
        query.query_id,
        n,
        response.count_band,
    )
    return response


class SearchService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def execute(self, query: CanonicalSearchQuery) -> tuple[list[ClinicalEvidenceRecord], str]:
        records = self.repository.list_evidence()
        matches = search_records(records, query)
        return matches, query_fingerprint(query)
