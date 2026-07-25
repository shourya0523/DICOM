"""Structured-field exclusion, normalisation, and regex PHI safety masking."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from provider_gateway.config import age_bucket
from provider_gateway.models import StudyRecord

EXCLUDED_FIELDS = {
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "StudyID",
    "StudyInstanceUID",
    "Diagnosis",
    "InstitutionName",
}

BODY_PART_ALIASES = {
    "HEAD": "BRAIN",
    "BRAIN": "BRAIN",
    "CARDIAC": "HEART",
    "CHEST": "HEART",
    "HEART": "HEART",
    "FETAL": "FETAL",
    "FOETAL": "FETAL",
    "FETUS": "FETAL",
}

MODALITY_ALIASES = {
    "MRI": "MR",
    "MR": "MR",
    "CT": "CT",
    "ULTRASOUND": "US",
    "US": "US",
}

_SPLIT_RE = re.compile(r"[,;|\\]+")

UID_RE = re.compile(r"\b1(?:\.\d+){4,}\b")
DATE_RE = re.compile(r"\b(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")
MRN_RE = re.compile(r"\b(?:MRN|ID)[:\s#]*[A-Z0-9-]{5,}\b", re.IGNORECASE)
LONG_ID_RE = re.compile(r"\b[A-Z]{2,5}-\d{4,}\b")
NAME_CARET_RE = re.compile(r"\b[A-Z][A-Za-z'-]+\^[A-Z][A-Za-z'-]+\b")


def make_study_token(provider_code: str, study_id: str, token_secret: str) -> str:
    digest = hashlib.sha256(
        f"{provider_code}{study_id}{token_secret}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{provider_code.lower()}-{digest}"


def parse_age_years(patient_age: str) -> float:
    raw = (patient_age or "").strip().upper()
    match = re.fullmatch(r"(\d{1,3})([YMD])", raw)
    if not match:
        return 0.0
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "Y":
        return float(value)
    if unit == "M":
        return round(value / 12.0, 3)
    return round(value / 365.0, 4)


def parse_study_year(study_date: str) -> int:
    text = (study_date or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return 0


def _split_raw_values(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return _SPLIT_RE.split(value)


def normalise_modalities(value: str | list[str] | None) -> list[str]:
    raw_values = _split_raw_values(value)
    normalised = {
        MODALITY_ALIASES.get(item.strip().upper(), item.strip().upper())
        for item in raw_values
        if item and item.strip()
    }
    return sorted(normalised)


def normalise_body_parts(value: str | list[str] | None) -> list[str]:
    raw_values = _split_raw_values(value)
    normalised = {
        BODY_PART_ALIASES.get(item.strip().upper(), item.strip().upper())
        for item in raw_values
        if item and item.strip()
    }
    return sorted(normalised)


# Backwards-compatible aliases for callers still using singular helpers.
def normalize_modality(modality: str) -> str:
    values = normalise_modalities(modality)
    return values[0] if values else "UNK"


def normalize_body_part(body_part: str) -> str:
    values = normalise_body_parts(body_part)
    return values[0] if values else "UNK"


def normalize_sex(sex: str) -> str:
    key = (sex or "").strip().upper()
    if key in {"M", "F", "O"}:
        return key
    return "U"


def safety_mask(text: str) -> str:
    if not text:
        return ""
    masked = text
    for pattern in (
        UID_RE,
        EMAIL_RE,
        PHONE_RE,
        DATE_RE,
        MRN_RE,
        LONG_ID_RE,
        NAME_CARET_RE,
    ):
        masked = pattern.sub("[REDACTED]", masked)
    return masked


def extract_gestational_age_weeks(
    diagnosis: str, body_parts: list[str]
) -> int | None:
    """Extract gestational age only for fetal studies."""
    if "FETAL" not in body_parts:
        return None
    if not diagnosis:
        return None
    match = re.search(
        r"(?:gestational\s+age|ga)[:\s]*(\d{1,2})\s*(?:weeks?|wks?)?",
        diagnosis,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(r"\b(\d{1,2})\s*(?:weeks?|wks?)\s*(?:ga|gestation)", diagnosis, re.IGNORECASE)
    if not match:
        return None
    weeks = int(match.group(1))
    if 1 <= weeks <= 45:
        return weeks
    return None


def structured_fields(study: StudyRecord) -> dict[str, Any]:
    """Return only non-identifying structured fields used for CER construction."""
    age_years = parse_age_years(study.PatientAge)
    return {
        "age_years": age_years,
        "age_bucket": age_bucket(age_years),
        "sex": normalize_sex(study.PatientSex),
        "study_year": parse_study_year(study.StudyDate),
        "modalities": normalise_modalities(study.Modality),
        "body_parts": normalise_body_parts(study.BodyPartExamined),
        "source_study_id": study.StudyID,
    }


def assert_no_excluded_fields(payload: dict[str, Any]) -> None:
    leaked = EXCLUDED_FIELDS.intersection(payload.keys())
    if leaked:
        raise ValueError(f"Excluded fields present in public payload: {sorted(leaked)}")
