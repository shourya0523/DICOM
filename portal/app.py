"""Central portal — platform SSO + gateway fan-out; product UI + /lab test dashboard."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from portal.gateway import (
    build_gateway_request,
    build_gateway_request_from_filters,
    call_gateway_search,
    gateway_to_dashboard_card,
    gateway_urls,
    use_real_gateway_protocol,
)
from portal.platform_auth import (
    PlatformLoginRequest,
    login as platform_login,
    verify_platform_token,
)
from portal.synonyms import expand
from shared.contracts import (
    NODE_PORTS,
    PORTAL_PORT,
    SUPPRESSION_THRESHOLD,
    DEMO_PROFILES,
    PortalRetrieveRequest,
    PortalSearchRequest,
    ResearcherProfile,
)

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

DEFAULT_NODES = {
    name: os.environ.get(f"{name}_URL", f"http://127.0.0.1:{port}")
    for name, port in NODE_PORTS.items()
}


def node_urls() -> dict[str, str]:
    return dict(DEFAULT_NODES)


app = FastAPI(
    title="Federated DICOM Search Portal",
    description="Platform SSO + hospital gateway fan-out (gateway owns hospital SSO/PII).",
    version="3.0.0",
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


def _apply_portal_suppression(results: list[dict[str, Any]]) -> dict[str, Any]:
    contributing = []
    for r in results:
        if r.get("status") == "ok" and isinstance(r.get("count"), int) and r["count"] > 0:
            contributing.append(r)

    aggregate_count = sum(r["count"] for r in contributing)
    portal_suppressed = False
    portal_reason = None

    if (
        len(contributing) <= 1
        and contributing
        and contributing[0]["count"] < SUPPRESSION_THRESHOLD
    ):
        portal_suppressed = True
        portal_reason = "rare cohort protection (aggregate)"
        aggregate_count = None

    return {
        "aggregate_count": aggregate_count,
        "portal_suppressed": portal_suppressed,
        "portal_reason": portal_reason,
        "nodes": results,
    }


async def _login(client: httpx.AsyncClient, base: str, researcher: ResearcherProfile) -> dict[str, Any]:
    resp = await client.post(f"{base.rstrip('/')}/auth/login", json=researcher.model_dump())
    if resp.status_code == 403:
        detail = resp.json().get("detail", resp.text)
        if isinstance(detail, dict):
            return detail
        return {"status": "denied_at_sso", "detail": str(detail)}
    resp.raise_for_status()
    return resp.json()


class PreviewRequest(BaseModel):
    q: str


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "portal",
        "port": PORTAL_PORT,
        "gateway_mode": "remote" if use_real_gateway_protocol() else "mock_local_nodes",
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
    return {key: p.model_dump() for key, p in DEMO_PROFILES.items()}


@app.post("/gateway/preview")
def gateway_preview(req: PreviewRequest, claims: dict = Depends(require_platform)):
    """Map NL → gateway filters without fan-out (for UI pre-fill)."""
    gateway_req = build_gateway_request(req.q)
    return {
        "gateway_request": gateway_req,
        "expanded_terms": expand(req.q),
        "platform_user": claims.get("sub"),
    }


@app.post("/search")
async def search(req: PortalSearchRequest, claims: dict = Depends(require_platform)):
    if req.filters is not None:
        gateway_req = build_gateway_request_from_filters(req.filters.model_dump())
        q_out = req.q or ""
        expanded = expand(q_out) if q_out else []
    elif req.q:
        gateway_req = build_gateway_request(req.q)
        q_out = req.q
        expanded = expand(req.q)
    else:
        raise HTTPException(status_code=400, detail="provide q and/or filters")

    urls = gateway_urls()
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            call_gateway_search(
                client,
                provider=name,
                base=base,
                gateway_req=gateway_req,
                researcher=req.researcher,
            )
            for name, base in urls.items()
        ]
        gateway_results = await asyncio.gather(*tasks)

    cards = [gateway_to_dashboard_card(g) for g in gateway_results]
    payload = _apply_portal_suppression(cards)
    payload["q"] = q_out
    payload["expanded_terms"] = expanded
    payload["researcher"] = req.researcher.model_dump()
    payload["platform_user"] = claims.get("sub")
    payload["gateway_request"] = gateway_req
    payload["gateway_responses"] = [
        {k: v for k, v in g.items() if not str(k).startswith("_")} for g in gateway_results
    ]
    return payload


@app.post("/retrieve")
async def retrieve(req: PortalRetrieveRequest, claims: dict = Depends(require_platform)):
    urls = node_urls()
    base = urls.get(req.node)
    if not base:
        raise HTTPException(status_code=400, detail=f"unknown node: {req.node}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        login = await _login(client, base, req.researcher)
        if login.get("status") == "denied_at_sso" or "access_token" not in login:
            raise HTTPException(
                status_code=403,
                detail=login if isinstance(login, dict) else {"status": "denied_at_sso"},
            )
        token = login["access_token"]
        resp = await client.get(
            f"{base.rstrip('/')}/retrieve/{req.study_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 403:
            raise HTTPException(status_code=403, detail=resp.json().get("detail", resp.text))
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail=resp.json().get("detail", resp.text))
        resp.raise_for_status()
        data = resp.json()
        data["platform_user"] = claims.get("sub")
        return data


@app.get("/audit/{node}")
async def proxy_audit(node: str, claims: dict = Depends(require_platform)):
    urls = node_urls()
    base = urls.get(node.upper())
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
