"""Merge user-supplied filters with Gemini-deduced filters.

Rule: whatever the frontend passed in is the truth and wins. Gemini only fills
fields the user left empty (null scalar / missing / empty list). Every value —
from either source — is normalized against the controlled vocab, so a resolved
filter can never contain a value outside the allowed types.

Also emits per-field provenance so the frontend can show what was pre-filled.
"""
from app.vocab import normalize_body_parts, normalize_concepts, normalize_modalities

SCALAR_FIELDS = (
    "patient_age_min",
    "patient_age_max",
    "gestational_age_min_weeks",
    "gestational_age_max_weeks",
)


def _pick_scalar(field, user, gemini, provenance):
    uv = user.get(field)
    if uv is not None:
        provenance[field] = "user"
        return uv
    gv = gemini.get(field)
    provenance[field] = "gemini" if gv is not None else "none"
    return gv


def _pick_list(field, normalizer, user, gemini, provenance):
    uv = normalizer(user.get(field))
    if uv:
        provenance[field] = "user"
        return uv
    gv = normalizer(gemini.get(field))
    provenance[field] = "gemini" if gv else "none"
    return gv


def resolve_filters(user_filters: dict | None, gemini_filters: dict | None) -> tuple[dict, dict]:
    """Return (resolved_filters, provenance).

    provenance maps each field to "user", "gemini", or "none" (neither had it).
    """
    user = user_filters or {}
    gemini = gemini_filters or {}
    provenance: dict[str, str] = {}

    resolved = {f: _pick_scalar(f, user, gemini, provenance) for f in SCALAR_FIELDS}
    resolved["modality"] = _pick_list("modality", normalize_modalities, user, gemini, provenance)
    resolved["body_part"] = _pick_list("body_part", normalize_body_parts, user, gemini, provenance)
    resolved["concepts"] = _pick_list("concepts", normalize_concepts, user, gemini, provenance)
    return resolved, provenance
