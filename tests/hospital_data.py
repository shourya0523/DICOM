"""Load real hospital StudyRecords for tests — no hardcoded clinical text."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from provider_gateway.config import CONCEPT_VOCAB
from provider_gateway.models import StudyRecord

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "hospitals"

HOSPITAL_FILES = {
    "BCH": DATA_DIR / "bch_data.json",
    "MGH": DATA_DIR / "mgh_data.json",
    "BWH": DATA_DIR / "bwh_data.json",
}


@lru_cache(maxsize=8)
def load_hospital_studies(hospital: str = "BCH") -> tuple[StudyRecord, ...]:
    path = HOSPITAL_FILES[hospital.upper()]
    raw = json.loads(path.read_text())
    return tuple(StudyRecord.model_validate(row) for row in raw)


def study_by_id(study_id: str, hospital: str = "BCH") -> StudyRecord:
    for study in load_hospital_studies(hospital):
        if study.StudyID == study_id:
            return study
    raise KeyError(f"{study_id} not found in {hospital} data")


def studies_mentioning(code: str, hospital: str = "BCH") -> list[StudyRecord]:
    """Return studies whose Diagnosis contains any CONCEPT_VOCAB pattern for code."""
    patterns = [p.lower() for p in CONCEPT_VOCAB[code]["patterns"]]
    hits: list[StudyRecord] = []
    for study in load_hospital_studies(hospital):
        text = study.Diagnosis.lower()
        if any(p in text for p in patterns):
            hits.append(study)
    return hits


def concept_codes() -> list[str]:
    return sorted(CONCEPT_VOCAB.keys())
