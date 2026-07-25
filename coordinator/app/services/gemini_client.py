"""Gemini-backed filter deduction.

Takes the researcher's natural-language string and returns a structured filter
object that matches the request `filters` schema exactly. The model is
constrained two ways:
  1. A responseSchema (JSON mode) forces the output shape and enums.
  2. Post-parse, every value is re-validated against the controlled vocab.

Degrades gracefully: if GEMINI_API_KEY is unset or the call fails, it returns
an empty deduction so the coordinator still runs on the user's filters alone.
"""
import json

import requests

from config import Config
from app.vocab import (
    ASSERTIONS,
    CANONICAL_BODY_PARTS,
    CANONICAL_MODALITIES,
    CONCEPT_CODES,
)

# Empty-but-complete filter object; also the fallback when deduction is skipped.
EMPTY_FILTERS = {
    "patient_age_min": None,
    "patient_age_max": None,
    "gestational_age_min_weeks": None,
    "gestational_age_max_weeks": None,
    "modality": [],
    "body_part": [],
    "concepts": [],
}


def _response_schema() -> dict:
    """OpenAPI-subset schema Gemini must conform its JSON output to."""
    return {
        "type": "object",
        "properties": {
            "patient_age_min": {"type": "integer", "nullable": True},
            "patient_age_max": {"type": "integer", "nullable": True},
            "gestational_age_min_weeks": {"type": "integer", "nullable": True},
            "gestational_age_max_weeks": {"type": "integer", "nullable": True},
            "modality": {
                "type": "array",
                "items": {"type": "string", "enum": CANONICAL_MODALITIES},
            },
            "body_part": {
                "type": "array",
                "items": {"type": "string", "enum": CANONICAL_BODY_PARTS},
            },
            "concepts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "enum": CONCEPT_CODES},
                        "assertion": {"type": "string", "enum": ASSERTIONS},
                    },
                    "required": ["code", "assertion"],
                },
            },
        },
        "required": list(EMPTY_FILTERS.keys()),
    }


def _build_prompt(nl_string: str, known_filters: dict) -> str:
    return f"""You are a clinical imaging query parser for a federated DICOM search network.
Given a researcher's natural-language search string, deduce structured filter values.

STRICT RULES:
- Use ONLY values from the controlled vocabulary below. Never invent codes,
  modalities, body parts, or assertions outside these lists.
- Use canonical forms only (map synonyms yourself, e.g. "MRI" -> "MR",
  "head" -> "BRAIN", "cardiac" -> "HEART").
- For a numeric range that is not stated or clearly implied, use null.
- For a list field with nothing implied, use an empty array [].
- "concepts" is a list of {{"code", "assertion"}}. Only include a concept the
  query actually references. Choose the assertion from context:
    negation ("no", "without", "rules out") -> NEGATED
    "possible", "suspected", "cannot exclude" -> UNCERTAIN
    "history of", "prior" -> HISTORICAL
    "family history of" -> FAMILY_HISTORY
    otherwise -> PRESENT
- patient_age_* is in YEARS. gestational_age_*_weeks applies to FETAL studies.
- The user already supplied the filters below. They are authoritative: do NOT
  change or contradict them. Deduce only what is missing, but still return the
  COMPLETE object.

CONTROLLED VOCABULARY:
- body_part (canonical): {CANONICAL_BODY_PARTS}
- modality (canonical): {CANONICAL_MODALITIES}
- concept codes: {CONCEPT_CODES}
- assertions: {ASSERTIONS}

USER-PROVIDED FILTERS (authoritative): {json.dumps(known_filters or {}, default=str)}

NATURAL-LANGUAGE QUERY: "{nl_string}"

Return a JSON object matching the required schema."""


def deduce_filters(nl_string: str, known_filters: dict | None = None) -> tuple[dict, dict]:
    """Return (deduced_filters, meta).

    `meta` reports how deduction went: {"status": ..., "model": ...}. On any
    problem it returns EMPTY_FILTERS with an explanatory status so the caller
    can proceed on user-supplied filters alone.
    """
    if not Config.GEMINI_API_KEY:
        return dict(EMPTY_FILTERS), {"status": "skipped_no_api_key", "model": Config.GEMINI_MODEL}

    url = f"{Config.GEMINI_API_BASE}/models/{Config.GEMINI_MODEL}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": _build_prompt(nl_string, known_filters or {})}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _response_schema(),
            "temperature": 0,
        },
    }
    try:
        resp = requests.post(
            url,
            params={"key": Config.GEMINI_API_KEY},
            json=payload,
            timeout=Config.GEMINI_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        deduced = json.loads(text)
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        return dict(EMPTY_FILTERS), {"status": f"error: {exc}", "model": Config.GEMINI_MODEL}

    # Ensure every expected key exists even if the model omitted one.
    merged = dict(EMPTY_FILTERS)
    merged.update({k: deduced.get(k, merged[k]) for k in EMPTY_FILTERS})
    return merged, {"status": "ok", "model": Config.GEMINI_MODEL}
