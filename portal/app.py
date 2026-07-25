"""Central portal — fans out to hospital nodes and brokers results to the user."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from portal.synonyms import expand
from shared.contracts import (
    NODE_PORTS,
    PORTAL_PORT,
    SUPPRESSION_THRESHOLD,
    DEMO_PROFILES,
    PortalRetrieveRequest,
    PortalSearchRequest,
    QueryRequest,
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
    description="Central broker: fans out queries to hospital nodes under zero-trust SSO.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _apply_portal_suppression(results: list[dict[str, Any]]) -> dict[str, Any]:
    """If only one node contributes a small nonsuppressed nonzero count, suppress aggregate."""
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


async def _query_node(
    client: httpx.AsyncClient,
    name: str,
    base: str,
    researcher: ResearcherProfile,
    q: str,
    expanded: list[str],
) -> dict[str, Any]:
    try:
        login = await _login(client, base, researcher)
        if login.get("status") == "denied_at_sso" or "access_token" not in login:
            return {
                "node": name,
                "status": "denied",
                "tier": "none",
                "count": None,
                "studies": [],
                "reason": login.get("detail", "denied_at_sso"),
                "sso": "denied_at_sso",
            }

        token = login["access_token"]
        body = QueryRequest(q=q, expanded_terms=expanded).model_dump()
        resp = await client.post(
            f"{base.rstrip('/')}/query",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        data["sso"] = "ok"
        data["scope"] = login.get("scope", [])
        return data
    except httpx.HTTPError as exc:
        return {
            "node": name,
            "status": "denied",
            "tier": "none",
            "count": None,
            "studies": [],
            "reason": f"node unreachable: {exc}",
            "sso": "error",
        }


@app.get("/health")
def health():
    return {"status": "healthy", "service": "portal", "port": PORTAL_PORT}


@app.get("/profiles")
def profiles():
    return {key: p.model_dump() for key, p in DEMO_PROFILES.items()}


@app.post("/search")
async def search(req: PortalSearchRequest):
    expanded = expand(req.q)
    urls = node_urls()
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            _query_node(client, name, base, req.researcher, req.q, expanded)
            for name, base in urls.items()
        ]
        results = await asyncio.gather(*tasks)

    payload = _apply_portal_suppression(list(results))
    payload["q"] = req.q
    payload["expanded_terms"] = expanded
    payload["researcher"] = req.researcher.model_dump()
    return payload


@app.post("/retrieve")
async def retrieve(req: PortalRetrieveRequest):
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
        return resp.json()


@app.get("/audit/{node}")
async def proxy_audit(node: str):
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
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
