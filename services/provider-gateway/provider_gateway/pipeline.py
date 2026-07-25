"""Transform one raw hospital study into one privacy-safe indexed CER."""

from __future__ import annotations

import logging

from provider_gateway import PIPELINE_VERSION
from provider_gateway.config import Settings
from provider_gateway.hospital_client import HospitalClient, HospitalNodeError
from provider_gateway.models import ClinicalEvidenceRecord, RefreshResponse, StudyRecord
from provider_gateway.openmed_adapter import OpenMedAdapter
from provider_gateway.redaction import (
    extract_gestational_age_weeks,
    make_study_token,
    structured_fields,
)
from provider_gateway.repository import Repository
from provider_gateway.config import utcnow

logger = logging.getLogger("provider_gateway.pipeline")


class IngestionPipeline:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        hospital_client: HospitalClient,
        openmed_adapter: OpenMedAdapter | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.hospital_client = hospital_client
        self.openmed = openmed_adapter or OpenMedAdapter()

    def refresh(self, *, warm_models: bool = True) -> RefreshResponse:
        if warm_models:
            self.openmed.warm()

        try:
            studies = self.hospital_client.fetch_studies()
        except HospitalNodeError as exc:
            status = "unavailable" if exc.unavailable else "failed"
            logger.info("INGESTION_COMPLETED status=%s", status)
            return RefreshResponse(
                provider=self.settings.provider_code,
                status=status,
                detail="Hospital node unavailable",
            )

        ingested = 0
        skipped = 0
        failed = 0
        for study in studies:
            try:
                record, source_id = self._process(study)
                self.repository.upsert_evidence(record, source_id)
                ingested += 1
            except Exception:
                failed += 1
                logger.warning("GATEWAY_SKIP_STUDY")

        if failed and ingested:
            status = "partial"
        elif failed and not ingested:
            status = "failed"
        else:
            status = "ok"

        logger.info(
            "INGESTION_COMPLETED provider=%s ingested=%s skipped=%s failed=%s",
            self.settings.provider_code,
            ingested,
            skipped,
            failed,
        )
        self.repository.log_audit(
            "INGESTION_COMPLETED",
            {
                "provider": self.settings.provider_code,
                "ingested": ingested,
                "failed": failed,
                "status": status,
            },
        )
        return RefreshResponse(
            provider=self.settings.provider_code,
            status=status,
            fetched=len(studies),
            ingested=ingested,
            skipped=skipped,
            failed=failed,
            index_timestamp=utcnow(),
        )

    def _process(self, study: StudyRecord) -> tuple[ClinicalEvidenceRecord, str]:
        fields = structured_fields(study)
        study_token = make_study_token(
            self.settings.provider_code,
            fields["source_study_id"],
            self.settings.token_secret,
        )
        diagnosis = study.Diagnosis
        gestational_age_weeks = extract_gestational_age_weeks(
            diagnosis, fields["body_parts"]
        )
        adapter_result = self.openmed.process_diagnosis(diagnosis)
        del diagnosis

        record = ClinicalEvidenceRecord(
            study_token=study_token,
            provider=self.settings.provider_code,
            age_years=fields["age_years"],
            age_bucket=fields["age_bucket"],
            sex=fields["sex"],
            study_year=fields["study_year"],
            modalities=fields["modalities"],
            body_parts=fields["body_parts"],
            gestational_age_weeks=gestational_age_weeks,
            concepts=adapter_result.concepts,
            pipeline_version=PIPELINE_VERSION,
            extraction_status=adapter_result.extraction_status,
        )
        return record, fields["source_study_id"]
