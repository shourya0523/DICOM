"""Controlled vocabulary for query filters.

Single source of truth for allowed filter values. Both the Gemini response
schema and the post-deduction validation are derived from this, so nothing —
whether typed by the user or deduced by the model — can ever land in a resolved
filter unless it appears here. Everything is normalized to canonical form.
"""

VOCAB = {
    "body_parts": {
        "canonical": ["BRAIN", "FETAL", "HEART"],
        "aliases": {
            "HEAD": "BRAIN",
            "BRAIN": "BRAIN",
            "CARDIAC": "HEART",
            "HEART": "HEART",
            "CHEST": "HEART",
            "FETAL": "FETAL",
            "FETUS": "FETAL",
        },
    },
    "modalities": {
        "canonical": ["MR", "CT", "US", "XR"],
        "aliases": {
            "MRI": "MR",
            "MR": "MR",
            "CT": "CT",
            "US": "US",
            "XR": "XR",
        },
    },
    "concepts": [
        "HYDROCEPHALUS", "VENTRICULOMEGALY", "INTRACRANIAL_HEMORRHAGE",
        "ISCHEMIC_INFARCT", "BRAIN_MASS", "CHIARI", "EDEMA",
        "CARDIOMYOPATHY", "MYOCARDIAL_FIBROSIS", "CONGENITAL_HEART_DISEASE",
        "NEURAL_TUBE_DEFECT", "SHUNT",
    ],
    "assertions": [
        "PRESENT", "NEGATED", "UNCERTAIN", "HISTORICAL", "FAMILY_HISTORY",
    ],
}

# Flattened, convenient views ------------------------------------------------
CANONICAL_BODY_PARTS = VOCAB["body_parts"]["canonical"]
CANONICAL_MODALITIES = VOCAB["modalities"]["canonical"]
CONCEPT_CODES = VOCAB["concepts"]
ASSERTIONS = VOCAB["assertions"]

_BODY_PART_ALIASES = {k.upper(): v for k, v in VOCAB["body_parts"]["aliases"].items()}
_MODALITY_ALIASES = {k.upper(): v for k, v in VOCAB["modalities"]["aliases"].items()}
# Friendly assertion synonyms that map onto the canonical set.
_ASSERTION_ALIASES = {"ABSENT": "NEGATED", "ABSET": "NEGATED", "PRESENT": "PRESENT"}


# Normalizers ----------------------------------------------------------------
def normalize_body_part(value) -> str | None:
    """Map a raw body-part value to canonical form, or None if unrecognized."""
    if value is None:
        return None
    return _BODY_PART_ALIASES.get(str(value).strip().upper())


def normalize_modality(value) -> str | None:
    """Map a raw modality value to canonical form, or None if unrecognized."""
    if value is None:
        return None
    return _MODALITY_ALIASES.get(str(value).strip().upper())


def normalize_concept(entry) -> dict | None:
    """Validate/normalize a {code, assertion} concept entry.

    Returns None if the code is not in the controlled vocabulary. An invalid or
    missing assertion falls back to PRESENT rather than dropping the concept.
    """
    if not isinstance(entry, dict):
        return None
    code = str(entry.get("code", "")).strip().upper()
    if code not in CONCEPT_CODES:
        return None
    raw_assertion = str(entry.get("assertion", "")).strip().upper()
    assertion = _ASSERTION_ALIASES.get(raw_assertion, raw_assertion)
    if assertion not in ASSERTIONS:
        assertion = "PRESENT"
    return {"code": code, "assertion": assertion}


def normalize_modalities(values) -> list[str]:
    return [m for v in (values or []) if (m := normalize_modality(v))]


def normalize_body_parts(values) -> list[str]:
    return [b for v in (values or []) if (b := normalize_body_part(v))]


def normalize_concepts(values) -> list[dict]:
    return [c for v in (values or []) if (c := normalize_concept(v))]
