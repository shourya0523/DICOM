"""Static synonym table for semantic query expansion."""

from __future__ import annotations

# Maps user terms → expansions that help match Diagnosis / BodyPart / Modality text
SYNONYMS: dict[str, list[str]] = {
    "tumor": ["neoplasm", "glioma", "mass", "astrocytoma", "meningioma"],
    "neoplasm": ["tumor", "glioma", "mass", "astrocytoma"],
    "glioma": ["tumor", "neoplasm", "astrocytoma"],
    "brain": ["cerebral", "neuro", "BRAIN"],
    "mri": ["MR", "magnetic resonance"],
    "pediatric": ["paediatric", "child", "neonatal"],
    "hydrocephalus": ["ventriculomegaly", "enlarged ventricles"],
    "ms": ["multiple sclerosis", "demyelinating"],
    "multiple sclerosis": ["ms", "demyelinating", "dawson's fingers"],
    "stroke": ["infarct", "ischemic", "mca"],
    "heart": ["cardiac", "HEART"],
    "fetal": ["foetal", "FETAL"],
}


def expand(q: str) -> list[str]:
    """Return unique expanded terms including the original query tokens."""
    tokens = [t for t in q.lower().replace(",", " ").split() if t]
    out: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            out.append(term)

    add(q.strip())
    for token in tokens:
        add(token)
        for syn in SYNONYMS.get(token, []):
            add(syn)
    # phrase-level lookups
    lower_q = q.lower().strip()
    for phrase, syns in SYNONYMS.items():
        if " " in phrase and phrase in lower_q:
            add(phrase)
            for syn in syns:
                add(syn)
    return out
