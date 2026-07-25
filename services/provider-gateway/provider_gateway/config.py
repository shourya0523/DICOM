"""Environment-driven Gateway settings and locked MVP concept vocabulary."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


CONCEPT_VOCAB: dict[str, dict[str, Any]] = {
    "HYDROCEPHALUS": {
        "display": "Hydrocephalus",
        "patterns": ["hydrocephalus"],
    },
    "VENTRICULOMEGALY": {
        "display": "Ventriculomegaly",
        "patterns": ["ventriculomegaly"],
    },
    "INTRACRANIAL_HEMORRHAGE": {
        "display": "Intracranial Hemorrhage",
        "patterns": ["hemorrhage", "haemorrhage", "bleed"],
    },
    "ISCHEMIC_INFARCT": {
        "display": "Ischemic Infarct",
        "patterns": ["infarct", "ischemia", "ischaemia"],
    },
    "BRAIN_MASS": {
        "display": "Brain Mass",
        "patterns": ["mass", "lesion", "tumor", "tumour", "glioma", "astrocytoma"],
    },
    "CHIARI": {
        "display": "Chiari Malformation",
        "patterns": ["chiari"],
    },
    "EDEMA": {
        "display": "Edema",
        "patterns": ["edema", "oedema"],
    },
    "CARDIOMYOPATHY": {
        "display": "Cardiomyopathy",
        "patterns": ["cardiomyopathy"],
    },
    "MYOCARDIAL_FIBROSIS": {
        "display": "Myocardial Fibrosis",
        "patterns": ["fibrosis"],
    },
    "CONGENITAL_HEART_DISEASE": {
        "display": "Congenital Heart Disease",
        "patterns": ["congenital heart", "asd", "vsd", "tetralogy"],
    },
    "NEURAL_TUBE_DEFECT": {
        "display": "Neural Tube Defect",
        "patterns": ["neural tube", "encephalocele", "anencephaly"],
    },
    "SHUNT": {
        "display": "Shunt",
        "patterns": ["shunt"],
    },
}

ALLOWED_METADATA_FIELDS = [
    "provider",
    "match_count",
    "count_band",
    "modalities",
    "body_parts",
    "study_years",
    "pipeline_version",
]

ALLOWED_DATA_FIELDS = [
    "study_token",
    "age_bucket",
    "gestational_age_weeks",
    "sex",
    "modalities",
    "body_parts",
    "study_year",
    "concepts",
]

AGE_BUCKETS = ("<1", "1-10", "11-17", "18-21", "22-40", "41-65", "66+")

COUNT_BANDS = (
    (0, 0, "0"),
    (1, 9, "<10"),
    (10, 24, "10-24"),
    (25, 49, "25-49"),
    (50, 99, "50-99"),
    (100, 249, "100-249"),
    (250, None, "250+"),
)

DEFAULT_BOOTSTRAP_ORGS = [
    {
        "organisation_id": "demo-research-lab",
        "display_name": "Demo Research Lab",
        "status": "ACTIVE",
        "metadata_auto_approval": True,
        "data_auto_approval": True,
        "allowed_metadata_fields": ALLOWED_METADATA_FIELDS,
        "allowed_data_fields": ALLOWED_DATA_FIELDS,
        "valid_from": "2020-01-01T00:00:00+00:00",
        "valid_to": "2099-12-31T23:59:59+00:00",
        "policy_version": "v1",
    },
    {
        "organisation_id": "partner-hospital-network",
        "display_name": "Partner Hospital Network",
        "status": "ACTIVE",
        "metadata_auto_approval": True,
        "data_auto_approval": False,
        "allowed_metadata_fields": ALLOWED_METADATA_FIELDS,
        "allowed_data_fields": ALLOWED_DATA_FIELDS,
        "valid_from": "2020-01-01T00:00:00+00:00",
        "valid_to": "2099-12-31T23:59:59+00:00",
        "policy_version": "v1",
    },
]


def count_band(n: int) -> str:
    for lo, hi, label in COUNT_BANDS:
        if hi is None:
            if n >= lo:
                return label
        elif lo <= n <= hi:
            return label
    return "250+"


def age_bucket(age_years: float) -> str:
    if age_years < 1:
        return "<1"
    if age_years <= 10:
        return "1-10"
    if age_years <= 17:
        return "11-17"
    if age_years <= 21:
        return "18-21"
    if age_years <= 40:
        return "22-40"
    if age_years <= 65:
        return "41-65"
    return "66+"


@dataclass
class Settings:
    provider_code: str
    provider_name: str
    node_url: str
    database_path: str
    token_secret: str
    service_api_key: str
    cohort_ttl_hours: int = 72
    http_timeout_seconds: float = 30.0
    bootstrap_orgs: list[dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_BOOTSTRAP_ORGS))

    @classmethod
    def from_env(cls) -> Settings:
        provider_code = os.environ.get("PROVIDER_CODE", "BCH").upper().strip()
        provider_name = os.environ.get(
            "PROVIDER_NAME",
            {
                "BCH": "Boston Children's Hospital",
                "MGH": "Massachusetts General Hospital",
                "BWH": "Brigham and Women's Hospital",
            }.get(provider_code, provider_code),
        )
        default_ports = {"BCH": 8001, "MGH": 8002, "BWH": 8003}
        port = default_ports.get(provider_code, 8001)
        node_url = os.environ.get("NODE_URL", f"http://localhost:{port}").rstrip("/")
        database_path = os.environ.get(
            "DATABASE_PATH", f"./data/gateway/{provider_code.lower()}_gateway.db"
        )
        token_secret = os.environ.get("TOKEN_SECRET", "local-secret")
        service_api_key = os.environ.get("SERVICE_API_KEY", "demo-key")
        cohort_ttl_hours = int(os.environ.get("COHORT_TTL_HOURS", "72"))
        http_timeout_seconds = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "30"))

        bootstrap_raw = os.environ.get("BOOTSTRAP_ORGS_JSON")
        if bootstrap_raw:
            bootstrap_orgs = json.loads(bootstrap_raw)
        else:
            bootstrap_orgs = list(DEFAULT_BOOTSTRAP_ORGS)

        return cls(
            provider_code=provider_code,
            provider_name=provider_name,
            node_url=node_url,
            database_path=database_path,
            token_secret=token_secret,
            service_api_key=service_api_key,
            cohort_ttl_hours=cohort_ttl_hours,
            http_timeout_seconds=http_timeout_seconds,
            bootstrap_orgs=bootstrap_orgs,
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
