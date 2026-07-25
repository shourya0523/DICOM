"""Canonical imaging-search vocabulary for NL decode + gateway filters."""

from __future__ import annotations

VOCAB: dict = {
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
            # NL helpers
            "CEREBRAL": "BRAIN",
            "NEURO": "BRAIN",
            "FOETAL": "FETAL",
        },
    },
    "modalities": {
        "canonical": ["MR", "CT", "US", "XR"],
        "aliases": {
            "MRI": "MR",
            "MR": "MR",
            "CT": "CT",
            "US": "US",
            "ULTRASOUND": "US",
            "XR": "XR",
            "XRAY": "XR",
            "X-RAY": "XR",
        },
    },
    "concepts": [
        "HYDROCEPHALUS",
        "VENTRICULOMEGALY",
        "INTRACRANIAL_HEMORRHAGE",
        "ISCHEMIC_INFARCT",
        "BRAIN_MASS",
        "CHIARI",
        "EDEMA",
        "CARDIOMYOPATHY",
        "MYOCARDIAL_FIBROSIS",
        "CONGENITAL_HEART_DISEASE",
        "NEURAL_TUBE_DEFECT",
        "SHUNT",
    ],
    "assertions": [
        "PRESENT",
        "NEGATED",
        "UNCERTAIN",
        "HISTORICAL",
        "FAMILY_HISTORY",
    ],
}
