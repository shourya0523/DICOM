"""Local study search against Diagnosis / BodyPart / Modality / Age heuristics."""

from __future__ import annotations

import re
from typing import Iterable

from models import StudyRecord

_MODALITY_ALIASES = {
    "mri": "MR",
    "mr": "MR",
    "magnetic resonance": "MR",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def expand_query_terms(q: str, expanded_terms: list[str] | None = None) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in [q, *(expanded_terms or [])]:
        for token in _tokenize(raw):
            if token not in seen:
                seen.add(token)
                terms.append(token)
        # also keep multi-word phrases from expanded_terms / q lowercase
        phrase = raw.strip().lower()
        if phrase and phrase not in seen and " " in phrase:
            seen.add(phrase)
            terms.append(phrase)
    return terms


def _age_is_pediatric(age: str) -> bool:
    m = re.match(r"^(\d+)([YMD])$", age.upper())
    if not m:
        return False
    value, unit = int(m.group(1)), m.group(2)
    if unit == "Y":
        return value <= 21
    return True  # days/months → pediatric


def match_studies(
    studies: Iterable[StudyRecord],
    q: str,
    expanded_terms: list[str] | None = None,
) -> list[StudyRecord]:
    terms = expand_query_terms(q, expanded_terms)
    if not terms:
        return []

    wants_pediatric = any(t in {"pediatric", "paediatric", "child", "neonatal"} for t in terms)
    wants_brain = any(t in {"brain", "cerebral", "neuro"} for t in terms)
    wants_heart = any(t in {"heart", "cardiac"} for t in terms)
    wants_fetal = any(t in {"fetal", "foetal"} for t in terms)

    modality_filter: str | None = None
    for t in terms:
        if t in _MODALITY_ALIASES:
            modality_filter = _MODALITY_ALIASES[t]
            break

    # Diagnosis / free-text terms: exclude structural filters already applied
    structural = {
        "pediatric",
        "paediatric",
        "child",
        "neonatal",
        "brain",
        "cerebral",
        "neuro",
        "heart",
        "cardiac",
        "fetal",
        "foetal",
        "mri",
        "mr",
        "magnetic",
        "resonance",
    }
    text_terms = [t for t in terms if t not in structural and t not in _MODALITY_ALIASES]

    matched: list[StudyRecord] = []
    for study in studies:
        if modality_filter and study.Modality.upper() != modality_filter:
            continue
        if wants_brain and study.BodyPartExamined.upper() != "BRAIN":
            continue
        if wants_heart and study.BodyPartExamined.upper() != "HEART":
            continue
        if wants_fetal and study.BodyPartExamined.upper() != "FETAL":
            continue
        if wants_pediatric and not _age_is_pediatric(study.PatientAge):
            continue

        haystack = " ".join(
            [
                study.Diagnosis,
                study.BodyPartExamined,
                study.Modality,
                study.InstitutionName,
            ]
        ).lower()

        if text_terms:
            if not any(t in haystack for t in text_terms):
                continue
        elif not (wants_brain or wants_heart or wants_fetal or wants_pediatric or modality_filter):
            # bare free-text: any term must appear
            if not any(t in haystack for t in terms):
                continue

        matched.append(study)
    return matched
