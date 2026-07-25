"""Frozen Pydantic contracts for the Provider Gateway."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Assertion(str, Enum):
    PRESENT = "PRESENT"
    NEGATED = "NEGATED"
    UNCERTAIN = "UNCERTAIN"
    HISTORICAL = "HISTORICAL"
    FAMILY_HISTORY = "FAMILY_HISTORY"


class OrgStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class AccessRequestStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    PENDING_REVIEW = "PENDING_REVIEW"
    REJECTED = "REJECTED"
    GENERATING_DATASET = "GENERATING_DATASET"
    DELIVERY_READY = "DELIVERY_READY"
    GENERATION_FAILED = "GENERATION_FAILED"
    EXPIRED = "EXPIRED"
    DELIVERED = "DELIVERED"


class StudyRecord(BaseModel):
    PatientName: str
    PatientID: str
    PatientBirthDate: str
    PatientAge: str
    PatientSex: str
    InstitutionName: str
    StudyID: str
    StudyInstanceUID: str
    StudyDate: str
    Modality: str
    BodyPartExamined: str
    Diagnosis: str


class CodedConcept(BaseModel):
    code: str
    display: str
    assertion: Assertion
    confidence: float = Field(ge=0.0, le=1.0)
    extractor: Literal["openmed", "keyword_fallback"]


class ClinicalEvidenceRecord(BaseModel):
    study_token: str
    provider: str
    age_years: float
    age_bucket: str
    sex: str
    study_year: int
    modalities: list[str] = Field(default_factory=list)
    body_parts: list[str] = Field(default_factory=list)
    gestational_age_weeks: int | None = None
    concepts: list[CodedConcept] = Field(default_factory=list)
    pipeline_version: str
    extraction_status: Literal["ok", "fallback", "failed"]


class OpenMedAdapterResult(BaseModel):
    concepts: list[CodedConcept] = Field(default_factory=list)
    pii_entity_count: int = 0
    pii_types: list[str] = Field(default_factory=list)
    model_info: dict[str, Any] = Field(default_factory=dict)
    extraction_status: Literal["ok", "fallback", "failed"] = "failed"
    latency_ms: int = 0


class ConceptFilter(BaseModel):
    code: str
    assertion: Assertion = Assertion.PRESENT


class SearchFilters(BaseModel):
    age_max: int | None = None
    age_min: int | None = None
    modalities: list[str] = Field(default_factory=list)
    body_parts: list[str] = Field(default_factory=list)
    sex: str | None = None
    concepts: list[ConceptFilter] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_singular_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if "modalities" not in payload and payload.get("modality") is not None:
            payload["modalities"] = [payload["modality"]]
        if "body_parts" not in payload and payload.get("body_part") is not None:
            payload["body_parts"] = [payload["body_part"]]
        payload.pop("modality", None)
        payload.pop("body_part", None)
        return payload


class CanonicalSearchQuery(BaseModel):
    query_id: str
    filters: SearchFilters = Field(default_factory=SearchFilters)
    freeze_cohort: bool = True


class SearchAggregateResponse(BaseModel):
    provider: str
    query_id: str
    match_count: int
    count_band: str
    modalities: list[str] = Field(default_factory=list)
    body_parts: list[str] = Field(default_factory=list)
    index_timestamp: datetime | None = None
    access_available: bool = True
    cohort_handle: str | None = None


class Cohort(BaseModel):
    cohort_handle: str
    query_id: str
    query_fingerprint: str
    member_tokens: list[str] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime
    index_version: str


class OrganisationPolicy(BaseModel):
    organisation_id: str
    display_name: str
    status: OrgStatus
    metadata_auto_approval: bool
    data_auto_approval: bool
    allowed_metadata_fields: list[str] = Field(default_factory=list)
    allowed_data_fields: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    policy_version: str = "v1"


class OrganisationPolicyCreate(BaseModel):
    organisation_id: str
    display_name: str
    status: OrgStatus = OrgStatus.ACTIVE
    metadata_auto_approval: bool = False
    data_auto_approval: bool = False
    allowed_metadata_fields: list[str] = Field(default_factory=list)
    allowed_data_fields: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    policy_version: str = "v1"


class OrganisationPolicyUpdate(BaseModel):
    display_name: str | None = None
    status: OrgStatus | None = None
    metadata_auto_approval: bool | None = None
    data_auto_approval: bool | None = None
    allowed_metadata_fields: list[str] | None = None
    allowed_data_fields: list[str] | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    policy_version: str | None = None


class AccessDecisionRequest(BaseModel):
    reason: str | None = None
    approved_fields: list[str] | None = None
    actor: str = "hospital_admin"


class AdminMetaResponse(BaseModel):
    provider: str
    provider_name: str
    allowed_metadata_fields: list[str]
    allowed_data_fields: list[str]
    pending_review_count: int = 0


class AccessRequestEvent(BaseModel):
    timestamp: datetime
    from_status: str | None
    to_status: str
    reason: str
    actor: str = "gateway"


class DatasetPreviewRow(BaseModel):
    study_token: str
    age_bucket: str
    sex: str
    modalities: list[str] = Field(default_factory=list)
    body_parts: list[str] = Field(default_factory=list)
    gestational_age_weeks: int | None = None
    study_year: int
    concepts: list[CodedConcept] = Field(default_factory=list)


class DatasetPreview(BaseModel):
    dataset_id: str
    cohort_handle: str
    provider: str
    match_count: int
    count_band: str
    modalities: list[str] = Field(default_factory=list)
    body_parts: list[str] = Field(default_factory=list)
    study_years: list[int] = Field(default_factory=list)
    pipeline_version: str
    rows: list[DatasetPreviewRow] = Field(default_factory=list)
    field_manifest: list[str] = Field(default_factory=list)


class AccessRequestCreate(BaseModel):
    coordinator_access_request_id: str
    organisation_id: str
    researcher_id: str
    cohort_handle: str
    project_title: str
    purpose: str
    requested_metadata_fields: list[str] = Field(default_factory=list)
    requested_data_fields: list[str] = Field(default_factory=list)


class AccessRequest(BaseModel):
    provider_request_id: str
    coordinator_access_request_id: str
    organisation_id: str
    researcher_id: str
    cohort_handle: str
    project_title: str
    purpose: str
    requested_metadata_fields: list[str] = Field(default_factory=list)
    requested_data_fields: list[str] = Field(default_factory=list)
    approved_fields: list[str] = Field(default_factory=list)
    status: AccessRequestStatus
    approval_path: str | None = None
    decision_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    dataset_id: str | None = None
    dataset_preview: DatasetPreview | None = None
    events: list[AccessRequestEvent] = Field(default_factory=list)


class RefreshResponse(BaseModel):
    provider: str
    status: Literal["ok", "partial", "failed", "unavailable"]
    fetched: int = 0
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    index_timestamp: datetime | None = None
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    provider: str
    provider_name: str
    node_reachable: bool | None = None
    indexed_studies: int = 0


class CapabilitiesResponse(BaseModel):
    provider: str
    provider_name: str
    pipeline_version: str
    concepts: list[str]
    age_buckets: list[str]
    allowed_metadata_fields: list[str]
    allowed_data_fields: list[str]
    endpoints: list[str]
