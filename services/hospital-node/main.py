"""Hospital node FastAPI app — SSO, query, retrieve, audit."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from auth import audit
from auth.deps import require_token
from auth.policies import is_email_allowed, query_tier_for, scopes_for, suppression_threshold
from auth.tokens import assert_distinct_demo_secrets, issue_token, secret_fingerprint, resolve_secret
from models import StudyRecord
from search import match_studies
from shared.contracts import (
    SCOPE_QUERY,
    SCOPE_RETRIEVE,
    LoginDenied,
    LoginSuccess,
    QueryRequest,
    QueryResponse,
    ResearcherProfile,
    RetrieveResponse,
)

NODE_DATA_MAP = {
    "BCH": "bch_data.json",
    "MGH": "mgh_data.json",
    "BWH": "bwh_data.json",
}

# Repo layout: <repo>/services/hospital-node/main.py -> <repo>/data/hospitals
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "hospitals"
DATA_DIR = Path(os.environ.get("HOSPITAL_DATA_DIR", DEFAULT_DATA_DIR))

HOSPITAL_NODE = os.environ.get("HOSPITAL_NODE", "").upper()

if HOSPITAL_NODE not in NODE_DATA_MAP:
    print(
        f"WARNING: HOSPITAL_NODE='{os.environ.get('HOSPITAL_NODE', '')}' "
        f"is not set or invalid. Valid values: {', '.join(NODE_DATA_MAP)}. "
        f"Defaulting to BCH.",
        file=sys.stderr,
    )
    HOSPITAL_NODE = "BCH"

assert_distinct_demo_secrets()
_secret = resolve_secret(HOSPITAL_NODE)
print(
    f"[auth] node={HOSPITAL_NODE} jwt_secret_fp={secret_fingerprint(_secret)}",
    file=sys.stderr,
)

data_path = DATA_DIR / NODE_DATA_MAP[HOSPITAL_NODE]
with open(data_path) as f:
    _raw = json.load(f)

studies: list[StudyRecord] = [StudyRecord(**record) for record in _raw]

app = FastAPI(
    title=f"Hospital Node — {HOSPITAL_NODE}",
    description="Zero-trust hospital edge node for federated DICOM search.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_get_claims = require_token(HOSPITAL_NODE)


def _redact_study(study: StudyRecord, *, full: bool) -> dict[str, Any]:
    data = study.model_dump()
    if not full:
        data.pop("PatientName", None)
        data.pop("PatientBirthDate", None)
    return data


@app.get("/health")
def health():
    return {"status": "healthy", "node": HOSPITAL_NODE}


@app.post("/auth/login")
def auth_login(profile: ResearcherProfile):
    email = profile.researcher_id.strip().lower()
    if not is_email_allowed(HOSPITAL_NODE, email):
        audit.record(
            researcher_id=email,
            action="login",
            decision="deny",
            reason="email not on node allowlist",
        )
        raise HTTPException(
            status_code=403,
            detail=LoginDenied(
                node=HOSPITAL_NODE,  # type: ignore[arg-type]
                detail="email not on node allowlist",
            ).model_dump(),
        )

    scopes = scopes_for(HOSPITAL_NODE, irb_approved=profile.irb_approved)
    token = issue_token(
        node=HOSPITAL_NODE,
        sub=email,
        org=profile.org,
        irb_approved=profile.irb_approved,
        scope=scopes,
    )
    audit.record(
        researcher_id=email,
        action="login",
        decision="allow",
        reason="sso allowlist match",
        detail=f"scopes={scopes}",
    )
    return LoginSuccess(
        access_token=token,
        scope=scopes,
        node=HOSPITAL_NODE,  # type: ignore[arg-type]
    )


@app.post("/query", response_model=QueryResponse)
def query(
    body: QueryRequest,
    claims: dict = Depends(_get_claims),
):
    researcher = claims.get("sub", "unknown")
    scopes = claims.get("scope") or []
    if SCOPE_QUERY not in scopes:
        audit.record(
            researcher_id=researcher,
            action="query",
            decision="deny",
            reason="missing imaging:query scope",
        )
        return QueryResponse(
            node=HOSPITAL_NODE,  # type: ignore[arg-type]
            status="denied",
            tier="none",
            reason="missing imaging:query scope",
        )

    matched = match_studies(studies, body.q, body.expanded_terms)
    count = len(matched)
    threshold = suppression_threshold()

    if 0 < count < threshold:
        audit.record(
            researcher_id=researcher,
            action="query",
            decision="suppress",
            reason="rare cohort protection",
            detail=f"count={count} threshold={threshold}",
        )
        return QueryResponse(
            node=HOSPITAL_NODE,  # type: ignore[arg-type]
            status="suppressed",
            tier="none",
            count=None,
            studies=[],
            reason="rare cohort protection",
        )

    irb = bool(claims.get("irb_approved"))
    tier = query_tier_for(HOSPITAL_NODE, irb_approved=irb)

    if tier == "full_metadata":
        study_payloads = [_redact_study(s, full=True) for s in matched]
    else:
        study_payloads = []

    audit.record(
        researcher_id=researcher,
        action="query",
        decision="allow",
        reason=f"tier={tier}",
        detail=f"count={count} q={body.q!r}",
    )
    return QueryResponse(
        node=HOSPITAL_NODE,  # type: ignore[arg-type]
        status="ok",
        tier=tier,  # type: ignore[arg-type]
        count=count,
        studies=study_payloads,
        reason=None,
    )


@app.get("/retrieve/{study_id}", response_model=RetrieveResponse)
def retrieve(
    study_id: str,
    claims: dict = Depends(_get_claims),
):
    researcher = claims.get("sub", "unknown")
    scopes = claims.get("scope") or []
    if SCOPE_RETRIEVE not in scopes:
        audit.record(
            researcher_id=researcher,
            action="retrieve",
            decision="deny",
            reason="missing imaging:retrieve scope",
            detail=study_id,
        )
        raise HTTPException(
            status_code=403,
            detail=RetrieveResponse(
                node=HOSPITAL_NODE,  # type: ignore[arg-type]
                study_id=study_id,
                status="denied",
                reason="missing imaging:retrieve scope",
            ).model_dump(),
        )

    for study in studies:
        if study.StudyID == study_id or study.StudyInstanceUID == study_id:
            audit.record(
                researcher_id=researcher,
                action="retrieve",
                decision="allow",
                reason="retrieve authorized",
                detail=study_id,
            )
            return RetrieveResponse(
                node=HOSPITAL_NODE,  # type: ignore[arg-type]
                study_id=study.StudyID,
                study=_redact_study(study, full=True),
                status="ok",
            )

    raise HTTPException(status_code=404, detail=f"Study '{study_id}' not found on this node.")


@app.get("/audit")
def get_audit():
    return {"node": HOSPITAL_NODE, "events": [e.model_dump() for e in audit.list_events()]}


# Legacy endpoints — gated behind the same Bearer token (no unauthenticated PII leak)
@app.get("/api/studies")
def list_studies(claims: dict = Depends(_get_claims)):
    if SCOPE_QUERY not in (claims.get("scope") or []):
        raise HTTPException(status_code=403, detail="missing imaging:query scope")
    irb = bool(claims.get("irb_approved"))
    full = query_tier_for(HOSPITAL_NODE, irb_approved=irb) == "full_metadata"
    return [_redact_study(s, full=full) for s in studies]


@app.get("/api/studies/{study_id}")
def get_study(study_id: str, claims: dict = Depends(_get_claims)):
    if SCOPE_QUERY not in (claims.get("scope") or []):
        raise HTTPException(status_code=403, detail="missing imaging:query scope")
    irb = bool(claims.get("irb_approved"))
    full = query_tier_for(HOSPITAL_NODE, irb_approved=irb) == "full_metadata"
    for study in studies:
        if study.StudyID == study_id:
            return _redact_study(study, full=full)
    raise HTTPException(status_code=404, detail=f"Study '{study_id}' not found on this node.")
