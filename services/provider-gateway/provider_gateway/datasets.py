"""Build de-identified metadata and row-level dataset previews."""

from __future__ import annotations

import logging
import uuid

from provider_gateway import PIPELINE_VERSION
from provider_gateway.config import ALLOWED_DATA_FIELDS, count_band
from provider_gateway.models import (
    Cohort,
    DatasetPreview,
    DatasetPreviewRow,
)
from provider_gateway.repository import Repository

logger = logging.getLogger("provider_gateway.datasets")


class DatasetService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def generate_preview(
        self,
        *,
        provider: str,
        cohort: Cohort,
        max_rows: int = 5,
    ) -> DatasetPreview:
        records = self.repository.get_evidence_many(cohort.member_tokens)
        modalities = sorted(
            {modality for r in records for modality in r.modalities}
        )
        body_parts = sorted(
            {body_part for r in records for body_part in r.body_parts}
        )
        study_years = sorted({r.study_year for r in records})
        rows = [
            DatasetPreviewRow(
                study_token=r.study_token,
                age_bucket=r.age_bucket,
                sex=r.sex,
                modalities=list(r.modalities),
                body_parts=list(r.body_parts),
                gestational_age_weeks=r.gestational_age_weeks,
                study_year=r.study_year,
                concepts=list(r.concepts),
            )
            for r in records[:max_rows]
        ]
        preview = DatasetPreview(
            dataset_id=f"ds-{uuid.uuid4().hex[:12]}",
            cohort_handle=cohort.cohort_handle,
            provider=provider,
            match_count=len(records),
            count_band=count_band(len(records)),
            modalities=modalities,
            body_parts=body_parts,
            study_years=study_years,
            pipeline_version=PIPELINE_VERSION,
            rows=rows,
            field_manifest=list(ALLOWED_DATA_FIELDS),
        )
        logger.info(
            "DATASET_GENERATED dataset_id=%s rows=%s",
            preview.dataset_id,
            len(rows),
        )
        self.repository.log_audit(
            "DATASET_GENERATED",
            {
                "dataset_id": preview.dataset_id,
                "cohort_handle": cohort.cohort_handle,
                "row_count": len(rows),
            },
        )
        return preview
