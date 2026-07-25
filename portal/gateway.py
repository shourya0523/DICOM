"""Gateway contract adapter — teammate search schema + local-node fallback.

Hospital gateway owns SSO/PII. Portal only:
  1. Requires platform SSO
  2. Translates NL query → gateway filters
  3. Fans out POST /search to each gateway (or mocks via local nodes)
  4. Maps gateway responses back for the existing dashboard cards
"""

from __future__ import annotations

import itertools
import os
import re
from typing import Any

import httpx

from portal.synonyms import expand
from shared.contracts import NODE_PORTS, ResearcherProfile

_query_seq = itertools.count(1001)

# Optional real gateway bases, e.g. GATEWAY_BCH_URL=http://127.0.0.1:9001
def gateway_urls() -> dict[str, str]:
    urls: dict[str, str] = {}
    for name, port in NODE_PORTS.items():
        env = os.environ.get(f"GATEWAY_{name}_URL")
        if env:
            urls[name] = env.rstrip("/")
        else:
            # Fallback: treat local hospital node as gateway mock backend
            urls[name] = os.environ.get(f"{name}_URL", f"http://127.0.0.1:{port}").rstrip("/")
    return urls


def use_real_gateway_protocol() -> bool:
    """True when at least one GATEWAY_*_URL is configured."""
    return any(os.environ.get(f"GATEWAY_{n}_URL") for n in NODE_PORTS)


def next_query_id() -> str:
    return f"q-{next(_query_seq)}"


def nl_to_filters(q: str) -> dict[str, Any]:
    """Map natural language (+ synonyms) into teammate gateway filters."""
    terms = [t.lower() for t in expand(q)]
    joined = " ".join(terms)
    tokens = set(re.findall(r"[a-z0-9]+", joined))

    age_min, age_max = 0, 120
    if tokens & {"pediatric", "paediatric", "child", "neonatal"}:
        age_min, age_max = 0, 21

    modality = None
    if tokens & {"mri", "mr"} or "magnetic" in tokens:
        modality = "MR"

    body_part = None
    if tokens & {"brain", "cerebral", "neuro"}:
        body_part = "BRAIN"
    elif tokens & {"heart", "cardiac"}:
        body_part = "HEART"
    elif tokens & {"fetal", "foetal"}:
        body_part = "FETAL"

    # Prefer a clinical concept over structural tokens
    structural = {
        "pediatric",
        "paediatric",
        "child",
        "neonatal",
        "brain",
        "cerebral",
        "neuro",
        "heart",
        "cardiac",
        "fetal",
        "foetal",
        "mri",
        "mr",
        "magnetic",
        "resonance",
    }
    concept_candidates = [t for t in expand(q) if t.lower() not in structural and " " not in t]
    concept = None
    # Prefer known diagnosis-like terms from original query order
    for raw in re.findall(r"[A-Za-z][A-Za-z\-]+", q):
        if raw.lower() not in structural and len(raw) > 2:
            concept = raw.lower()
            break
    if not concept and concept_candidates:
        concept = concept_candidates[0].lower()

    filters: dict[str, Any] = {
        "age_min": age_min,
        "age_max": age_max,
    }
    if modality:
        filters["modality"] = modality
    if body_part:
        filters["body_part"] = body_part
    if concept:
        filters["concept"] = concept
    return filters


def build_gateway_request(q: str, query_id: str | None = None) -> dict[str, Any]:
    return {
        "query_id": query_id or next_query_id(),
        "filters": nl_to_filters(q),
    }


def count_band(n: int | None) -> str | None:
    if n is None:
        return None
    if n == 0:
        return "0"
    if n < 5:
        return "<5"
    bands = [
        (5, 9, "5-9"),
        (10, 24, "10-24"),
        (25, 49, "25-49"),
        (50, 99, "50-99"),
        (100, 249, "100-249"),
        (250, 999, "250-999"),
    ]
    for lo, hi, label in bands:
        if lo <= n <= hi:
            return label
    return "1000+"


def node_result_to_gateway(
    *,
    provider: str,
    node_payload: dict[str, Any],
) -> dict[str, Any]:
    """Map our local node /query result into teammate gateway response shape."""
    status = node_payload.get("status")
    if status == "denied" or node_payload.get("sso") == "denied_at_sso":
        return {
            "provider": provider,
            "status": "denied",
            "match_count": None,
            "count_band": None,
            "sample_summary": None,
            "access_available": False,
            "reason": node_payload.get("reason") or "denied_at_sso",
        }
    if status == "suppressed":
        return {
            "provider": provider,
            "status": "suppressed",
            "match_count": None,
            "count_band": "<5",
            "sample_summary": {"modalities": ["MR"], "body_parts": ["BRAIN"]},
            "access_available": False,
            "reason": node_payload.get("reason") or "rare cohort protection",
        }

    count = node_payload.get("count")
    studies = node_payload.get("studies") or []
    modalities = sorted({s.get("Modality") for s in studies if s.get("Modality")}) or ["MR"]
    body_parts = sorted({s.get("BodyPartExamined") for s in studies if s.get("BodyPartExamined")})
    access = node_payload.get("tier") == "full_metadata" or bool(
        "imaging:retrieve" in (node_payload.get("scope") or [])
    )
    return {
        "provider": provider,
        "status": "complete",
        "match_count": count,
        "count_band": count_band(count if isinstance(count, int) else None),
        "sample_summary": {
            "modalities": modalities,
            "body_parts": body_parts or [],
        },
        "access_available": access,
        # Keep studies for dashboard retrieve links when mock gateway allows full metadata
        "_studies": studies,
        "_tier": node_payload.get("tier"),
        "_scope": node_payload.get("scope"),
        "_sso": node_payload.get("sso", "ok"),
    }


def gateway_to_dashboard_card(gw: dict[str, Any]) -> dict[str, Any]:
    """Map gateway response → existing dashboard node card fields."""
    status_map = {
        "complete": "ok",
        "ok": "ok",
        "suppressed": "suppressed",
        "denied": "denied",
        "denied_at_sso": "denied",
    }
    status = status_map.get(str(gw.get("status")), "denied")
    tier = "none"
    if status == "ok":
        tier = "full_metadata" if gw.get("access_available") else "count_only"
    return {
        "node": gw.get("provider") or gw.get("node"),
        "status": status,
        "tier": gw.get("_tier") or tier,
        "count": gw.get("match_count"),
        "studies": gw.get("_studies") or [],
        "reason": gw.get("reason"),
        "sso": gw.get("_sso") or ("ok" if status != "denied" else "denied_at_sso"),
        "scope": gw.get("_scope") or [],
        "gateway": {
            "status": gw.get("status"),
            "match_count": gw.get("match_count"),
            "count_band": gw.get("count_band"),
            "sample_summary": gw.get("sample_summary"),
            "access_available": gw.get("access_available"),
        },
    }


async def call_gateway_search(
    client: httpx.AsyncClient,
    *,
    provider: str,
    base: str,
    gateway_req: dict[str, Any],
    researcher: ResearcherProfile,
) -> dict[str, Any]:
    """POST teammate-shaped /search when GATEWAY_* set; else mock via local node SSO+/query."""
    if os.environ.get(f"GATEWAY_{provider}_URL"):
        try:
            resp = await client.post(
                f"{base}/search",
                json=gateway_req,
                headers={
                    # Gateway owns SSO — forward platform identity as hint if they want it
                    "X-Researcher-Id": researcher.researcher_id,
                    "X-Researcher-Org": researcher.org,
                    "X-IRB-Approved": "true" if researcher.irb_approved else "false",
                },
            )
            if resp.status_code >= 400:
                return {
                    "provider": provider,
                    "status": "denied",
                    "match_count": None,
                    "count_band": None,
                    "sample_summary": None,
                    "access_available": False,
                    "reason": f"gateway HTTP {resp.status_code}",
                }
            data = resp.json()
            data.setdefault("provider", provider)
            return data
        except httpx.HTTPError as exc:
            return {
                "provider": provider,
                "status": "denied",
                "match_count": None,
                "count_band": None,
                "sample_summary": None,
                "access_available": False,
                "reason": f"gateway unreachable: {exc}",
            }

    # --- Mock gateway using local hospital nodes (gateway SSO ≈ node SSO) ---
    try:
        login_resp = await client.post(
            f"{base}/auth/login",
            json=researcher.model_dump(),
        )
        if login_resp.status_code == 403:
            detail = login_resp.json().get("detail", login_resp.text)
            reason = detail.get("detail") if isinstance(detail, dict) else str(detail)
            return node_result_to_gateway(
                provider=provider,
                node_payload={
                    "status": "denied",
                    "sso": "denied_at_sso",
                    "reason": reason or "denied_at_sso",
                },
            )
        login_resp.raise_for_status()
        login = login_resp.json()
        if "access_token" not in login:
            return node_result_to_gateway(
                provider=provider,
                node_payload={
                    "status": "denied",
                    "sso": "denied_at_sso",
                    "reason": "denied_at_sso",
                },
            )

        # Rebuild NL query from filters for the local matcher
        filters = gateway_req.get("filters") or {}
        parts = []
        if filters.get("age_max") is not None and filters.get("age_max") <= 21:
            parts.append("pediatric")
        if filters.get("body_part"):
            parts.append(str(filters["body_part"]).lower())
        if filters.get("modality") == "MR":
            parts.append("MRI")
        if filters.get("concept"):
            parts.append(str(filters["concept"]))
        q = " ".join(parts) or "brain"

        resp = await client.post(
            f"{base}/query",
            json={"q": q, "expanded_terms": expand(q)},
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
        resp.raise_for_status()
        payload = resp.json()
        payload["sso"] = "ok"
        payload["scope"] = login.get("scope", [])
        return node_result_to_gateway(provider=provider, node_payload=payload)
    except httpx.HTTPError as exc:
        return {
            "provider": provider,
            "status": "denied",
            "match_count": None,
            "count_band": None,
            "sample_summary": None,
            "access_available": False,
            "reason": f"node unreachable: {exc}",
        }
