"""Entity extraction tests against real hospital JSON diagnoses."""

from __future__ import annotations

import json
import os

import pytest

# Keep default suite offline/fast — keyword path uses the same vocab + assertion rules.
os.environ.setdefault("OPENMED_FORCE_FALLBACK", "1")

from provider_gateway.config import CONCEPT_VOCAB
from provider_gateway.models import Assertion, StudyRecord
from provider_gateway.openmed_adapter import OpenMedAdapter, negex_assertion
from provider_gateway.pipeline import IngestionPipeline
from provider_gateway.redaction import structured_fields
from provider_gateway.repository import build_repository
from provider_gateway.config import Settings

from tests.hospital_data import (
    concept_codes,
    load_hospital_studies,
    studies_mentioning,
    study_by_id,
)


@pytest.fixture(scope="module")
def adapter() -> OpenMedAdapter:
    return OpenMedAdapter()


def _expected_assertion_for_code(adapter: OpenMedAdapter, text: str, code: str) -> Assertion:
    """Derive expected assertion from shared NegEx-lite helper."""
    lower = text.lower()
    for pattern in CONCEPT_VOCAB[code]["patterns"]:
        idx = lower.find(pattern.lower())
        if idx < 0:
            continue
        return negex_assertion(text, idx, idx + len(pattern))
    raise AssertionError(f"pattern for {code} not found in diagnosis")


def _concepts_by_code(adapter: OpenMedAdapter, study: StudyRecord) -> dict[str, Assertion]:
    result = adapter.process_diagnosis(study.Diagnosis)
    return {c.code: c.assertion for c in result.concepts}


# StudyIDs pulled from data/bch_data.json — expectations computed at runtime from Diagnosis.
PRESENT_CASES = [
    ("BR-7890", "HYDROCEPHALUS"),
    ("BR-8839", "HYDROCEPHALUS"),
    ("FT-9892", "VENTRICULOMEGALY"),
    ("BR-1543", "ISCHEMIC_INFARCT"),
    ("BR-1543", "EDEMA"),
    ("FT-8120", "CHIARI"),
    ("HT-8921", "CARDIOMYOPATHY"),
    ("BR-1039", "VENTRICULOMEGALY"),
]

NEGATED_CASES = [
    ("BR-3498", "HYDROCEPHALUS"),
    ("BR-3498", "VENTRICULOMEGALY"),
    ("FT-2190", "INTRACRANIAL_HEMORRHAGE"),
    ("HT-6023", "CARDIOMYOPATHY"),
    ("HT-9943", "SHUNT"),
    ("HT-5116", "EDEMA"),
    ("BR-6124", "ISCHEMIC_INFARCT"),
]


@pytest.mark.parametrize("study_id,code", PRESENT_CASES)
def test_extracts_present_concept_from_real_study(
    adapter: OpenMedAdapter, study_id: str, code: str
) -> None:
    study = study_by_id(study_id)
    expected = _expected_assertion_for_code(adapter, study.Diagnosis, code)
    assert expected == Assertion.PRESENT

    by_code = _concepts_by_code(adapter, study)
    assert code in by_code
    assert by_code[code] == Assertion.PRESENT


@pytest.mark.parametrize("study_id,code", NEGATED_CASES)
def test_extracts_negated_concept_from_real_study(
    adapter: OpenMedAdapter, study_id: str, code: str
) -> None:
    study = study_by_id(study_id)
    expected = _expected_assertion_for_code(adapter, study.Diagnosis, code)
    assert expected == Assertion.NEGATED

    by_code = _concepts_by_code(adapter, study)
    assert code in by_code
    assert by_code[code] == Assertion.NEGATED


@pytest.mark.parametrize("code", concept_codes())
def test_every_vocab_concept_appears_in_bch_data(code: str) -> None:
    hits = studies_mentioning(code, "BCH")
    assert hits, f"{code} has no matches in BCH Diagnosis text — vocab/data drift"


@pytest.mark.parametrize("code", concept_codes())
def test_adapter_extracts_each_vocab_concept_from_matching_studies(
    adapter: OpenMedAdapter, code: str
) -> None:
    hits = studies_mentioning(code, "BCH")
    sample = hits[:8]
    assert sample
    extracted = 0
    for study in sample:
        by_code = _concepts_by_code(adapter, study)
        if code in by_code:
            extracted += 1
            expected = _expected_assertion_for_code(adapter, study.Diagnosis, code)
            assert by_code[code] == expected
    assert extracted >= 1, f"adapter never emitted {code} for {len(sample)} matching studies"


def test_body_part_coverage_in_bch() -> None:
    studies = load_hospital_studies("BCH")
    parts = {s.BodyPartExamined for s in studies}
    assert {"BRAIN", "HEART", "FETAL"}.issubset(parts)


def test_pipeline_on_real_studies_strips_phi(tmp_path, adapter: OpenMedAdapter) -> None:
    settings = Settings(
        provider_code="BCH",
        provider_name="Boston Children's Hospital",
        node_url="http://localhost:9001",
        database_path=str(tmp_path / "extract.db"),
        token_secret="test-secret",
        service_api_key="demo-key",
    )
    repo = build_repository(settings)
    sample_ids = [sid for sid, _ in PRESENT_CASES[:4]] + [sid for sid, _ in NEGATED_CASES[:3]]
    studies = [study_by_id(sid) for sid in dict.fromkeys(sample_ids)]

    class FakeClient:
        def fetch_studies(self):
            return studies

    pipeline = IngestionPipeline(settings, repo, FakeClient(), adapter)  # type: ignore[arg-type]
    result = pipeline.refresh(warm_models=False)
    assert result.status == "ok"
    assert result.ingested == len(studies)

    for record in repo.list_evidence():
        blob = json.dumps(record.model_dump())
        assert "Diagnosis" not in blob
        assert "PatientName" not in blob
        assert "PatientID" not in blob
        assert "StudyInstanceUID" not in blob
        for study in studies:
            assert study.PatientName not in blob
            assert study.PatientID not in blob
            assert study.StudyID not in blob
            assert study.StudyInstanceUID not in blob
        assert record.concepts
        study = next(
            s for s in studies if make_token_match(settings, s, record.study_token)
        )
        fields = structured_fields(study)
        assert record.age_bucket == fields["age_bucket"]
        assert record.body_parts == fields["body_parts"]
        assert record.modalities == fields["modalities"]
        assert "modality" not in record.model_dump()
        assert "body_part" not in record.model_dump()


def make_token_match(settings: Settings, study: StudyRecord, token: str) -> bool:
    from provider_gateway.redaction import make_study_token

    return make_study_token(settings.provider_code, study.StudyID, settings.token_secret) == token


def test_multi_concept_report_from_real_data(adapter: OpenMedAdapter) -> None:
    """BR-1543 mentions infarct + edema; extraction should surface both."""
    study = study_by_id("BR-1543")
    by_code = _concepts_by_code(adapter, study)
    assert "ISCHEMIC_INFARCT" in by_code
    assert "EDEMA" in by_code
    assert by_code["ISCHEMIC_INFARCT"] == Assertion.PRESENT
    assert by_code["EDEMA"] == Assertion.PRESENT


def test_chiari_present_does_not_force_ventriculomegaly_present(
    adapter: OpenMedAdapter,
) -> None:
    """BR-3498: Chiari present, ventriculomegaly explicitly without."""
    study = study_by_id("BR-3498")
    by_code = _concepts_by_code(adapter, study)
    assert by_code.get("CHIARI") == Assertion.PRESENT
    assert by_code.get("VENTRICULOMEGALY") == Assertion.NEGATED
    assert by_code.get("HYDROCEPHALUS") == Assertion.NEGATED


@pytest.mark.parametrize("hospital", ["BCH", "MGH", "BWH"])
def test_adapter_runs_on_sample_from_each_hospital(
    adapter: OpenMedAdapter, hospital: str
) -> None:
    studies = load_hospital_studies(hospital)[:15]
    assert len(studies) == 15
    ok = 0
    for study in studies:
        result = adapter.process_diagnosis(study.Diagnosis)
        assert result.extraction_status in {"ok", "fallback", "failed"}
        # Never echo source identifiers into concept displays.
        for concept in result.concepts:
            assert concept.code in CONCEPT_VOCAB
            assert study.PatientID not in concept.display
            assert study.StudyID not in concept.display
        ok += 1
    assert ok == 15


LIVE_OPENMED = os.environ.get("OPENMED_LIVE", "").lower() in {"1", "true", "yes"}


@pytest.mark.skipif(not LIVE_OPENMED, reason="Set OPENMED_LIVE=1 to run real OpenMed NER")
@pytest.mark.parametrize("study_id,code", [("BR-8839", "HYDROCEPHALUS"), ("BR-3498", "HYDROCEPHALUS")])
def test_live_openmed_extraction_on_real_studies(study_id: str, code: str) -> None:
    os.environ["OPENMED_FORCE_FALLBACK"] = "0"
    try:
        live = OpenMedAdapter()
        live._available = None  # re-check import
        study = study_by_id(study_id)
        result = live.process_diagnosis(study.Diagnosis)
        assert result.extraction_status in {"ok", "fallback"}
        by_code = {c.code: c for c in result.concepts}
        assert code in by_code
        expected = _expected_assertion_for_code(live, study.Diagnosis, code)
        # Live ConText may differ slightly; require same polarity for clear cases.
        if expected == Assertion.NEGATED:
            assert by_code[code].assertion == Assertion.NEGATED
        else:
            assert by_code[code].assertion == Assertion.PRESENT
        assert by_code[code].extractor in {"openmed", "keyword_fallback"}
    finally:
        os.environ["OPENMED_FORCE_FALLBACK"] = "1"
