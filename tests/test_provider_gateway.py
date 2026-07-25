"""Unit and contract tests for the Provider Gateway."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Force keyword fallback so tests never download OpenMed models.
os.environ["OPENMED_FORCE_FALLBACK"] = "1"
os.environ["SERVICE_API_KEY"] = "demo-key"
os.environ["TOKEN_SECRET"] = "test-secret"
os.environ["PROVIDER_CODE"] = "BCH"
os.environ["PROVIDER_NAME"] = "Boston Children's Hospital"
os.environ["NODE_URL"] = "http://localhost:8001"

from provider_gateway.app import create_app
from provider_gateway.cohorts import CohortService
from provider_gateway.config import CONCEPT_VOCAB, Settings, count_band, utcnow
from provider_gateway.models import (
    AccessRequestStatus,
    Assertion,
    CanonicalSearchQuery,
    ClinicalEvidenceRecord,
    CodedConcept,
    ConceptFilter,
    SearchFilters,
    StudyRecord,
)
from provider_gateway.openmed_adapter import OpenMedAdapter
from provider_gateway.pipeline import IngestionPipeline
from provider_gateway.redaction import (
    extract_gestational_age_weeks,
    make_study_token,
    normalise_body_parts,
    normalise_modalities,
    parse_age_years,
    safety_mask,
    structured_fields,
)
from provider_gateway.repository import build_repository
from provider_gateway.search import (
    SearchService,
    matches_filters,
    query_fingerprint,
    search_records,
)


PHI_MARKERS = [
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "StudyInstanceUID",
    "Harrington",
    "Diagnosis",
]

OBSOLETE_SINGULAR_FIELDS = {"modality", "body_part"}


def _cer(
    *,
    study_token: str = "bch-aaaa1111",
    modalities: list[str] | None = None,
    body_parts: list[str] | None = None,
    age_years: float = 7,
    age_bucket: str = "1-10",
    sex: str = "M",
    study_year: int = 2026,
    concepts: list[CodedConcept] | None = None,
) -> ClinicalEvidenceRecord:
    return ClinicalEvidenceRecord(
        study_token=study_token,
        provider="BCH",
        age_years=age_years,
        age_bucket=age_bucket,
        sex=sex,
        study_year=study_year,
        modalities=list(modalities or ["MR"]),
        body_parts=list(body_parts or ["BRAIN"]),
        concepts=concepts
        or [
            CodedConcept(
                code="HYDROCEPHALUS",
                display="Hydrocephalus",
                assertion=Assertion.PRESENT,
                confidence=0.9,
                extractor="keyword_fallback",
            )
        ],
        pipeline_version="openmed-v1",
        extraction_status="fallback",
    )


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        provider_code="BCH",
        provider_name="Boston Children's Hospital",
        node_url="http://localhost:8001",
        database_path=str(tmp_path / "test_gateway.db"),
        token_secret="test-secret",
        service_api_key="demo-key",
    )


@pytest.fixture()
def repo(settings: Settings):
    return build_repository(settings)


@pytest.fixture()
def sample_study() -> StudyRecord:
    return StudyRecord(
        PatientName="Harrington^Lucas",
        PatientID="CHB-99214",
        PatientBirthDate="20181104",
        PatientAge="007Y",
        PatientSex="M",
        InstitutionName="Boston Children's Hospital",
        StudyID="BR-7721",
        StudyInstanceUID="1.3.12.2.1107.5.2.19.45152.20260715",
        StudyDate="20260715",
        Modality="MR",
        BodyPartExamined="BRAIN",
        Diagnosis="No evidence of hydrocephalus. Mild ventriculomegaly is present.",
    )


def test_parse_age_years():
    assert parse_age_years("007Y") == 7.0
    assert parse_age_years("006M") == 0.5
    assert parse_age_years("030D") == pytest.approx(30 / 365.0, rel=1e-3)


def test_normalise_modalities():
    assert normalise_modalities("MR") == ["MR"]
    assert normalise_modalities("MRI") == ["MR"]
    assert normalise_modalities("MR;CT") == ["CT", "MR"]
    assert normalise_modalities(["MR", "MR", "CT"]) == ["CT", "MR"]
    assert normalise_modalities(None) == []


def test_normalise_body_parts():
    assert normalise_body_parts("BRAIN") == ["BRAIN"]
    assert normalise_body_parts("HEAD") == ["BRAIN"]
    assert normalise_body_parts("HEAD;CERVICAL") == ["BRAIN", "CERVICAL"]
    assert normalise_body_parts("FOETAL") == ["FETAL"]
    assert normalise_body_parts(None) == []


def test_gestational_age_only_for_fetal():
    assert extract_gestational_age_weeks("Gestational age 32 weeks", ["BRAIN"]) is None
    assert (
        extract_gestational_age_weeks("Gestational age 32 weeks", ["FETAL"]) == 32
    )


def test_study_token_stable(sample_study: StudyRecord):
    t1 = make_study_token("BCH", sample_study.StudyID, "test-secret")
    t2 = make_study_token("BCH", sample_study.StudyID, "test-secret")
    assert t1 == t2
    assert t1.startswith("bch-")
    assert len(t1.split("-")[1]) == 8


def test_structured_fields_exclude_identifiers(sample_study: StudyRecord):
    fields = structured_fields(sample_study)
    for key in ("PatientName", "PatientID", "Diagnosis", "StudyInstanceUID"):
        assert key not in fields
    assert fields["age_bucket"] == "1-10"
    assert fields["modalities"] == ["MR"]
    assert fields["body_parts"] == ["BRAIN"]
    assert fields["study_year"] == 2026
    assert OBSOLETE_SINGULAR_FIELDS.isdisjoint(fields.keys())


def test_safety_mask_redacts_patterns():
    text = "Contact jane@hospital.org or 1.2.3.4.5.6.7.8 on 20260715. MRN: ABC-12345"
    masked = safety_mask(text)
    assert "jane@hospital.org" not in masked
    assert "1.2.3.4.5.6.7.8" not in masked
    assert "[REDACTED]" in masked


def test_negex_list_negation_and_without_scope():
    from provider_gateway.openmed_adapter import negex_assertion

    text = (
        "No acute infarction, intracranial hemorrhage, mass lesion, or abnormal "
        "enhancement is seen."
    )
    for term in ("infarction", "hemorrhage", "mass"):
        i = text.lower().find(term)
        assert negex_assertion(text, i, i + len(term)) == Assertion.NEGATED

    text2 = (
        "Impression: Chiari I malformation without associated ventriculomegaly "
        "or cervical syrinx."
    )
    c = text2.lower().find("chiari")
    v = text2.lower().find("ventriculomegaly")
    assert negex_assertion(text2, c, c + 6) == Assertion.PRESENT
    assert negex_assertion(text2, v, v + len("ventriculomegaly")) == Assertion.NEGATED


def test_merge_prefers_negated_and_unions_codes():
    from provider_gateway.openmed_adapter import merge_concepts

    ner = [
        CodedConcept(
            code="HYDROCEPHALUS",
            display="Hydrocephalus",
            assertion=Assertion.PRESENT,
            confidence=0.9,
            extractor="openmed",
        )
    ]
    kw = [
        CodedConcept(
            code="HYDROCEPHALUS",
            display="Hydrocephalus",
            assertion=Assertion.NEGATED,
            confidence=0.7,
            extractor="keyword_fallback",
        ),
        CodedConcept(
            code="SHUNT",
            display="Shunt",
            assertion=Assertion.PRESENT,
            confidence=0.7,
            extractor="keyword_fallback",
        ),
    ]
    merged = {c.code: c for c in merge_concepts(ner, kw)}
    assert merged["HYDROCEPHALUS"].assertion == Assertion.NEGATED
    assert merged["HYDROCEPHALUS"].extractor == "openmed"
    assert "SHUNT" in merged


def test_keyword_fallback_negation():
    adapter = OpenMedAdapter()
    result = adapter.process_diagnosis(
        "No evidence of hydrocephalus. Mild ventriculomegaly is present."
    )
    assert result.extraction_status == "fallback"
    by_code = {c.code: c for c in result.concepts}
    assert by_code["HYDROCEPHALUS"].assertion == Assertion.NEGATED
    assert by_code["VENTRICULOMEGALY"].assertion == Assertion.PRESENT


def test_count_band():
    assert count_band(0) == "0"
    assert count_band(7) == "<10"
    assert count_band(14) == "10-24"
    assert count_band(300) == "250+"


def test_search_list_overlap_semantics():
    mr_brain = _cer(study_token="bch-mr", modalities=["MR"], body_parts=["BRAIN"])
    us_heart = _cer(
        study_token="bch-us",
        modalities=["US"],
        body_parts=["HEART"],
        concepts=[
            CodedConcept(
                code="CARDIOMYOPATHY",
                display="Cardiomyopathy",
                assertion=Assertion.PRESENT,
                confidence=0.8,
                extractor="keyword_fallback",
            )
        ],
    )
    multi_part = _cer(
        study_token="bch-multi",
        modalities=["MR"],
        body_parts=["BRAIN", "CERVICAL"],
    )

    assert matches_filters(
        mr_brain,
        CanonicalSearchQuery(
            query_id="q",
            filters=SearchFilters(modalities=["MR", "CT"]),
        ),
    )
    assert not matches_filters(
        us_heart,
        CanonicalSearchQuery(
            query_id="q",
            filters=SearchFilters(modalities=["MR", "CT"]),
        ),
    )
    assert matches_filters(
        multi_part,
        CanonicalSearchQuery(
            query_id="q",
            filters=SearchFilters(body_parts=["CERVICAL"]),
        ),
    )
    assert matches_filters(
        mr_brain,
        CanonicalSearchQuery(
            query_id="q",
            filters=SearchFilters(modalities=[], body_parts=[]),
        ),
    )


def test_query_fingerprint_normalises_list_order():
    q1 = CanonicalSearchQuery(
        query_id="q-1",
        filters=SearchFilters(modalities=["MR", "CT"]),
    )
    q2 = CanonicalSearchQuery(
        query_id="q-1",
        filters=SearchFilters(modalities=["CT", "MR", "MR"]),
    )
    assert query_fingerprint(q1) == query_fingerprint(q2)


def test_search_and_filters(repo, settings):
    records = [
        _cer(
            study_token="bch-aaaa1111",
            modalities=["MR"],
            body_parts=["BRAIN"],
        ),
        _cer(
            study_token="bch-bbbb2222",
            modalities=["CT"],
            body_parts=["HEART"],
            age_years=40,
            age_bucket="22-40",
            sex="F",
            study_year=2025,
            concepts=[
                CodedConcept(
                    code="CARDIOMYOPATHY",
                    display="Cardiomyopathy",
                    assertion=Assertion.PRESENT,
                    confidence=0.8,
                    extractor="keyword_fallback",
                )
            ],
        ),
    ]
    for r in records:
        repo.upsert_evidence(r, source_study_id=r.study_token)

    query = CanonicalSearchQuery(
        query_id="q-1",
        filters=SearchFilters(
            age_max=21,
            modalities=["MR"],
            body_parts=["BRAIN"],
            concepts=[ConceptFilter(code="HYDROCEPHALUS", assertion=Assertion.PRESENT)],
        ),
    )
    matches = search_records(repo.list_evidence(), query)
    assert len(matches) == 1
    assert matches[0].study_token == "bch-aaaa1111"
    assert matches[0].modalities == ["MR"]
    assert matches[0].body_parts == ["BRAIN"]


def test_pipeline_persists_cer_without_phi(settings, repo, sample_study):
    class FakeClient:
        def fetch_studies(self):
            return [sample_study]

    pipeline = IngestionPipeline(
        settings, repo, FakeClient(), OpenMedAdapter()  # type: ignore[arg-type]
    )
    result = pipeline.refresh(warm_models=False)
    assert result.status == "ok"
    assert result.ingested == 1
    evidence = repo.list_evidence()
    assert len(evidence) == 1
    dumped = evidence[0].model_dump()
    assert dumped["modalities"] == ["MR"]
    assert dumped["body_parts"] == ["BRAIN"]
    assert OBSOLETE_SINGULAR_FIELDS.isdisjoint(dumped.keys())
    blob = json.dumps(dumped)
    for marker in ("Harrington", "CHB-99214", "1.3.12.2.1107", "No evidence"):
        assert marker not in blob


def test_access_auto_approve_and_preview(settings, repo):
    record = _cer(study_token="bch-cccc3333")
    repo.upsert_evidence(record, "BR-9999")

    cohort_service = CohortService(settings, repo)
    query = CanonicalSearchQuery(
        query_id="q-1001",
        filters=SearchFilters(
            concepts=[ConceptFilter(code="HYDROCEPHALUS", assertion=Assertion.PRESENT)]
        ),
    )
    matches, _ = SearchService(repo).execute(query)
    cohort = cohort_service.freeze(query, matches, index_version="openmed-v1")

    app = create_app(settings)
    client = TestClient(app)
    headers = {"X-API-Key": "demo-key"}

    create = {
        "coordinator_access_request_id": "coord-1",
        "organisation_id": "demo-research-lab",
        "researcher_id": "r-1",
        "cohort_handle": cohort.cohort_handle,
        "project_title": "Hydrocephalus cohort study",
        "purpose": "Feasibility analysis",
        "requested_metadata_fields": ["provider", "match_count", "count_band"],
        "requested_data_fields": [
            "study_token",
            "age_bucket",
            "sex",
            "modalities",
            "body_parts",
            "study_year",
            "concepts",
        ],
    }
    response = client.post("/access-requests", json=create, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AccessRequestStatus.DELIVERY_READY.value
    assert body["dataset_preview"] is not None
    assert len(body["dataset_preview"]["rows"]) <= 5
    row = body["dataset_preview"]["rows"][0]
    assert "modalities" in row
    assert "body_parts" in row
    assert OBSOLETE_SINGULAR_FIELDS.isdisjoint(row.keys())
    assert OBSOLETE_SINGULAR_FIELDS.isdisjoint(body["dataset_preview"].keys())
    blob = json.dumps(body)
    for marker in PHI_MARKERS + ["Harrington", "BR-9999"]:
        assert marker not in blob

    # Idempotent
    again = client.post("/access-requests", json=create, headers=headers)
    assert again.json()["provider_request_id"] == body["provider_request_id"]


def test_unknown_org_pending_review(settings, repo):
    from provider_gateway.models import Cohort

    now = utcnow()
    cohort = Cohort(
        cohort_handle="cohort-bch-deadbeef",
        query_id="q-x",
        query_fingerprint="abc",
        member_tokens=["bch-cccc3333"],
        created_at=now,
        expires_at=now + timedelta(hours=24),
        index_version="openmed-v1",
    )
    repo.save_cohort(cohort)
    app = create_app(settings)
    client = TestClient(app)
    response = client.post(
        "/access-requests",
        headers={"X-API-Key": "demo-key"},
        json={
            "coordinator_access_request_id": "coord-unknown",
            "organisation_id": "unknown-org",
            "researcher_id": "r-2",
            "cohort_handle": cohort.cohort_handle,
            "project_title": "Study",
            "purpose": "Research",
            "requested_metadata_fields": ["provider"],
            "requested_data_fields": ["study_token"],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == AccessRequestStatus.PENDING_REVIEW.value


def test_health_and_capabilities(settings):
    app = create_app(settings)
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["provider"] == "BCH"

    caps = client.get("/capabilities", headers={"X-API-Key": "demo-key"})
    assert caps.status_code == 200
    body = caps.json()
    assert set(CONCEPT_VOCAB.keys()).issubset(set(body["concepts"]))
    assert "modalities" in body["allowed_data_fields"]
    assert "body_parts" in body["allowed_data_fields"]
    assert "modality" not in body["allowed_data_fields"]
    assert "body_part" not in body["allowed_data_fields"]


def test_search_endpoint(settings, repo):
    repo.upsert_evidence(_cer(study_token="bch-dddd4444"), "BR-1")
    app = create_app(settings)
    client = TestClient(app)
    response = client.post(
        "/search",
        headers={"X-API-Key": "demo-key"},
        json={
            "query_id": "q-1001",
            "filters": {
                "age_max": 21,
                "modalities": ["MR"],
                "body_parts": ["BRAIN"],
                "concepts": [{"code": "HYDROCEPHALUS", "assertion": "PRESENT"}],
            },
            "freeze_cohort": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["match_count"] >= 1
    assert body["cohort_handle"]
    assert "count_band" in body
    assert body["modalities"] == ["MR"]
    assert body["body_parts"] == ["BRAIN"]
    assert OBSOLETE_SINGULAR_FIELDS.isdisjoint(body.keys())


def test_search_accepts_singular_request_aliases(settings, repo):
    repo.upsert_evidence(_cer(study_token="bch-alias"), "BR-alias")
    app = create_app(settings)
    client = TestClient(app)
    response = client.post(
        "/search",
        headers={"X-API-Key": "demo-key"},
        json={
            "query_id": "q-alias",
            "filters": {
                "modality": "MR",
                "body_part": "BRAIN",
            },
            "freeze_cohort": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["match_count"] >= 1
    assert "modalities" in body
    assert "body_parts" in body
    assert OBSOLETE_SINGULAR_FIELDS.isdisjoint(body.keys())


def test_admin_create_and_update_allowlist(settings, repo):
    app = create_app(settings)
    client = TestClient(app)
    headers = {"X-API-Key": "demo-key"}

    created = client.post(
        "/admin/organisations",
        headers=headers,
        json={
            "organisation_id": "wellness-lab",
            "display_name": "Wellness Lab",
            "status": "ACTIVE",
            "metadata_auto_approval": True,
            "data_auto_approval": False,
            "allowed_metadata_fields": ["provider", "match_count"],
            "allowed_data_fields": ["study_token", "sex"],
        },
    )
    assert created.status_code == 200
    assert created.json()["organisation_id"] == "wellness-lab"

    listed = client.get("/admin/organisations", headers=headers)
    assert listed.status_code == 200
    ids = {o["organisation_id"] for o in listed.json()}
    assert "wellness-lab" in ids

    updated = client.put(
        "/admin/organisations/wellness-lab",
        headers=headers,
        json={
            "data_auto_approval": True,
            "allowed_data_fields": ["study_token", "sex", "age_bucket"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["data_auto_approval"] is True
    assert "age_bucket" in updated.json()["allowed_data_fields"]

    bad = client.post(
        "/admin/organisations",
        headers=headers,
        json={
            "organisation_id": "bad-lab",
            "display_name": "Bad Lab",
            "allowed_metadata_fields": ["not_a_real_field"],
        },
    )
    assert bad.status_code == 400


def test_admin_approve_and_deny_pending_requests(settings, repo):
    from provider_gateway.models import Cohort

    now = utcnow()
    cohort = Cohort(
        cohort_handle="cohort-bch-admin01",
        query_id="q-admin",
        query_fingerprint="admin-fp",
        member_tokens=["bch-admin01"],
        created_at=now,
        expires_at=now + timedelta(hours=24),
        index_version="openmed-v1",
    )
    repo.save_cohort(cohort)
    repo.upsert_evidence(_cer(study_token="bch-admin01"), "BR-admin")

    app = create_app(settings)
    client = TestClient(app)
    headers = {"X-API-Key": "demo-key"}

    pending = client.post(
        "/access-requests",
        headers=headers,
        json={
            "coordinator_access_request_id": "coord-pending-1",
            "organisation_id": "partner-hospital-network",
            "researcher_id": "r-admin",
            "cohort_handle": cohort.cohort_handle,
            "project_title": "Manual review study",
            "purpose": "Needs hospital decision",
            "requested_metadata_fields": ["provider"],
            "requested_data_fields": ["study_token", "sex"],
        },
    )
    assert pending.status_code == 200
    body = pending.json()
    assert body["status"] == AccessRequestStatus.PENDING_REVIEW.value
    request_id = body["provider_request_id"]

    queue = client.get(
        "/admin/access-requests",
        headers=headers,
        params={"status": "PENDING_REVIEW"},
    )
    assert queue.status_code == 200
    assert any(r["provider_request_id"] == request_id for r in queue.json())

    approved = client.post(
        f"/admin/access-requests/{request_id}/approve",
        headers=headers,
        json={"reason": "Approved for pilot", "actor": "hospital_admin"},
    )
    assert approved.status_code == 200
    approved_body = approved.json()
    assert approved_body["status"] == AccessRequestStatus.DELIVERY_READY.value
    assert approved_body["approval_path"] == "manual"
    assert approved_body["dataset_preview"] is not None

    deny_cohort = Cohort(
        cohort_handle="cohort-bch-admin02",
        query_id="q-admin-2",
        query_fingerprint="admin-fp-2",
        member_tokens=["bch-admin01"],
        created_at=now,
        expires_at=now + timedelta(hours=24),
        index_version="openmed-v1",
    )
    repo.save_cohort(deny_cohort)
    pending2 = client.post(
        "/access-requests",
        headers=headers,
        json={
            "coordinator_access_request_id": "coord-pending-2",
            "organisation_id": "partner-hospital-network",
            "researcher_id": "r-admin-2",
            "cohort_handle": deny_cohort.cohort_handle,
            "project_title": "Denied study",
            "purpose": "Should be denied",
            "requested_metadata_fields": ["provider"],
            "requested_data_fields": ["study_token"],
        },
    )
    deny_id = pending2.json()["provider_request_id"]
    denied = client.post(
        f"/admin/access-requests/{deny_id}/deny",
        headers=headers,
        json={"reason": "Insufficient justification"},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == AccessRequestStatus.REJECTED.value
    assert denied.json()["approval_path"] == "manual"


def test_hospital_portal_served(settings):
    app = create_app(settings)
    client = TestClient(app)
    page = client.get("/portal")
    assert page.status_code == 200
    assert "Hospital Access Portal" in page.text
    css = client.get("/portal/assets/styles.css")
    assert css.status_code == 200
    assert "#059669" in css.text
