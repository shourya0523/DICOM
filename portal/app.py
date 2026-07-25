"""Central portal — platform SSO + UI; delegates search to the Coordinator service.

Reconciled architecture:
    frontend (this UI)  ->  portal (:8010)  ->  coordinator (:5001)  ->  gateway (stub) -> nodes

The portal no longer maps NL->filters or fans out to nodes itself. It:
  1. Gates access with platform SSO (unchanged).
  2. Translates the frontend request into the coordinator's inline /search
     contract, calls the coordinator (which runs Gemini filter deduction),
     and adapts the response back into the shape the dashboard already renders.

Self-contained on purpose: does NOT import shared/vocab.py or shared/contracts.py.
The coordinator is the single source of truth for vocabulary + filter contract.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from portal.platform_auth import (
    PlatformLoginRequest,
    login as platform_login,
    verify_platform_token,
)

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

PORTAL_PORT = 8010
NODE_PORTS = {"BCH": 8001, "MGH": 8002, "BWH": 8003}

# Where the coordinator (Flask brain) lives.
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "http://127.0.0.1:5001").rstrip("/")

DEFAULT_NODES = {
    name: os.environ.get(f"{name}_URL", f"http://127.0.0.1:{port}")
    for name, port in NODE_PORTS.items()
}

# Demo researcher profiles (portal UI presets) — inlined; no shared/contracts.py.
DEMO_PROFILES: dict[str, dict[str, Any]] = {
    "harvard_irb": {"researcher_id": "jorgenson@harvard.edu", "org": "Harvard University", "irb_approved": True},
    "mit_partner": {"researcher_id": "lee@mit.edu", "org": "MIT", "irb_approved": False},
    "neu": {"researcher_id": "patel@northeastern.edu", "org": "Northeastern University", "irb_approved": False},
    "bu": {"researcher_id": "chen@bu.edu", "org": "Boston University", "irb_approved": False},
    "guest": {"researcher_id": "guest@example.com", "org": "Public", "irb_approved": False},
}


# --- Inbound request models (frontend -> portal) ----------------------------
class ResearcherProfile(BaseModel):
    researcher_id: str
    org: str = ""
    irb_approved: bool = False


class Concept(BaseModel):
    code: str
    assertion: str = "PRESENT"


class FrontendFilters(BaseModel):
    """Shape the dashboard sends: modality is a string, body_parts is a list."""

    patient_age_min: Optional[int] = None
    patient_age_max: Optional[int] = None
    gestational_age_min_weeks: Optional[int] = None
    gestational_age_max_weeks: Optional[int] = None
    modality: Optional[str] = None
    body_parts: list[str] = Field(default_factory=list)
    concepts: list[Concept] = Field(default_factory=list)


class PortalSearchRequest(BaseModel):
    researcher: ResearcherProfile
    q: Optional[str] = None
    filters: Optional[FrontendFilters] = None


class PortalRetrieveRequest(BaseModel):
    node: str
    study_id: str
    researcher: ResearcherProfile


class PreviewRequest(BaseModel):
    q: str


# --- Filter shape translation (frontend <-> coordinator inline contract) -----
def _to_coordinator_filters(f: dict[str, Any] | None) -> dict[str, Any]:
    """Frontend filters -> coordinator inline filters (modality/body_part as lists)."""
    f = f or {}
    mod = f.get("modality")
    if isinstance(mod, list):
        modality = mod
    elif mod:
        modality = [mod]
    else:
        modality = []

    bp = f.get("body_parts")
    if bp is None:
        bp = [f["body_part"]] if f.get("body_part") else []
    body_part = bp if isinstance(bp, list) else ([bp] if bp else [])

    return {
        "patient_age_min": f.get("patient_age_min"),
        "patient_age_max": f.get("patient_age_max"),
        "gestational_age_min_weeks": f.get("gestational_age_min_weeks"),
        "gestational_age_max_weeks": f.get("gestational_age_max_weeks"),
        "modality": modality,
        "body_part": body_part,
        "concepts": f.get("concepts") or [],
    }


def _from_coordinator_filters(rf: dict[str, Any] | None) -> dict[str, Any]:
    """Coordinator resolved filters -> frontend filter shape (for form pre-fill)."""
    rf = rf or {}
    mods = rf.get("modality") or []
    bps = rf.get("body_part") or []
    return {
        "patient_age_min": rf.get("patient_age_min"),
        "patient_age_max": rf.get("patient_age_max"),
        "gestational_age_min_weeks": rf.get("gestational_age_min_weeks"),
        "gestational_age_max_weeks": rf.get("gestational_age_max_weeks"),
        "modality": mods[0] if mods else "",
        "body_parts": bps,
        "concepts": rf.get("concepts") or [],
    }


def _synth_nl(f: dict[str, Any] | None) -> str:
    """Build a minimal NL string from filters when the user gave no query text
    (coordinator requires a non-empty nl-string)."""
    f = f or {}
    parts: list[str] = []
    bps = f.get("body_parts") or ([f["body_part"]] if f.get("body_part") else [])
    parts += [str(b).lower() for b in bps]
    mod = f.get("modality")
    if isinstance(mod, list):
        parts += [str(m) for m in mod]
    elif mod:
        parts.append(str(mod))
    for c in f.get("concepts") or []:
        code = c.get("code") if isinstance(c, dict) else c
        if code:
            parts.append(str(code).replace("_", " ").lower())
    gmin, gmax = f.get("gestational_age_min_weeks"), f.get("gestational_age_max_weeks")
    if gmin or gmax:
        parts.append(f"{gmin or gmax} weeks gestational")
    return " ".join(parts).strip() or "imaging study"


async def _call_coordinator(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post(f"{COORDINATOR_URL}/search", json=payload)
    resp.raise_for_status()
    return resp.json()


# --- App --------------------------------------------------------------------
app = FastAPI(
    title="Federated DICOM Search Portal",
    description="Platform SSO + UI; search delegated to the coordinator service.",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_bearer = HTTPBearer(auto_error=False)


def require_platform(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="platform login required")
    return verify_platform_token(credentials.credentials)


def node_urls() -> dict[str, str]:
    return dict(DEFAULT_NODES)


async def _login(client: httpx.AsyncClient, base: str, researcher: ResearcherProfile) -> dict[str, Any]:
    resp = await client.post(f"{base.rstrip('/')}/auth/login", json=researcher.model_dump())
    if resp.status_code == 403:
        detail = resp.json().get("detail", resp.text)
        return detail if isinstance(detail, dict) else {"status": "denied_at_sso", "detail": str(detail)}
    resp.raise_for_status()
    return resp.json()


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "portal",
        "port": PORTAL_PORT,
        "coordinator_url": COORDINATOR_URL,
    }


@app.post("/platform/login")
def do_platform_login(req: PlatformLoginRequest):
    return platform_login(req)


@app.get("/platform/me")
def platform_me(claims: dict = Depends(require_platform)):
    return {
        "email": claims.get("sub"),
        "org": claims.get("org", ""),
        "display_name": claims.get("display_name", claims.get("sub")),
    }


@app.get("/profiles")
def profiles():
    return DEMO_PROFILES


@app.post("/gateway/preview")
async def gateway_preview(req: PreviewRequest, claims: dict = Depends(require_platform)):
    """NL -> deduced filters (via coordinator) without committing a search."""
    payload = {
        "query_id": f"q-{uuid4().hex[:8]}",
        "nl-string": req.q,
        "filters": _to_coordinator_filters({}),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        coord = await _call_coordinator(client, payload)
    return {
        "gateway_request": {"filters": _from_coordinator_filters(coord.get("resolved_filters"))},
        "expanded_terms": [],
        "platform_user": claims.get("sub"),
        "coordinator": {
            "gemini": coord.get("gemini"),
            "filter_provenance": coord.get("filter_provenance"),
        },
    }


@app.post("/search")
async def search(req: PortalSearchRequest, claims: dict = Depends(require_platform)):
    user_filters = req.filters.model_dump() if req.filters else {}
    q = (req.q or "").strip()
    nl = q or _synth_nl(user_filters)

    payload = {
        "query_id": f"q-{uuid4().hex[:8]}",
        "nl-string": nl,
        "filters": _to_coordinator_filters(user_filters),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        coord = await _call_coordinator(client, payload)

    resolved = coord.get("resolved_filters", {})
    results = coord.get("results", []) or []

    nodes: list[dict[str, Any]] = []
    gateway_responses: list[dict[str, Any]] = []
    for r in results:
        code = r.get("hospital_code") or r.get("node")
        reason = r.get("detail") or "awaiting gateway wiring"
        pending = r.get("status") == "stub"
        status = "pending" if pending else (r.get("status") or "denied")
        nodes.append(
            {"node": code, "status": status, "count": None, "tier": "none", "studies": [], "reason": reason}
        )
        gateway_responses.append(
            {
                "provider": code,
                "status": status,
                "match_count": None,
                "count_band": "awaiting gateway" if pending else None,
                "access_available": False,
                "sample_summary": None,
                "reason": reason,
            }
        )

    return {
        "q": nl,
        "researcher": req.researcher.model_dump(),
        "platform_user": claims.get("sub"),
        "gateway_request": {"query_id": payload["query_id"], "filters": _from_coordinator_filters(resolved)},
        "gateway_responses": gateway_responses,
        "nodes": nodes,
        "aggregate_count": None,
        "portal_suppressed": False,
        "portal_reason": None,
        "expanded_terms": [],
        "coordinator": {
            "gemini": coord.get("gemini"),
            "filter_provenance": coord.get("filter_provenance"),
            "resolved_filters": resolved,
        },
    }


@app.post("/retrieve")
async def retrieve(req: PortalRetrieveRequest, claims: dict = Depends(require_platform)):
    base = node_urls().get(req.node)
    if not base:
        raise HTTPException(status_code=400, detail=f"unknown node: {req.node}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        login = await _login(client, base, req.researcher)
        if login.get("status") == "denied_at_sso" or "access_token" not in login:
            raise HTTPException(status_code=403, detail=login)
        token = login["access_token"]
        resp = await client.get(
            f"{base.rstrip('/')}/retrieve/{req.study_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code in (403, 404):
            raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", resp.text))
        resp.raise_for_status()
        data = resp.json()
        data["platform_user"] = claims.get("sub")
        return data


@app.get("/audit/{node}")
async def proxy_audit(node: str, claims: dict = Depends(require_platform)):
    base = node_urls().get(node.upper())
    if not base:
        raise HTTPException(status_code=400, detail=f"unknown node: {node}")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{base.rstrip('/')}/audit")
        resp.raise_for_status()
        return resp.json()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "app" / "index.html")


@app.get("/lab")
def lab_index():
    return FileResponse(STATIC_DIR / "lab" / "index.html")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
