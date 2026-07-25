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
from shared.vocab import VOCAB

_query_seq = itertools.count(1001)

_BODY_ALIASES = {k.upper(): v for k, v in VOCAB["body_parts"]["aliases"].items()}
_MOD_ALIASES = {k.upper().replace("-", ""): v for k, v in VOCAB["modalities"]["aliases"].items()}
_CONCEPTS = list(VOCAB["concepts"])
_ASSERTIONS = set(VOCAB["assertions"])


def gateway_urls() -> dict[str, str]:
    urls: dict[str, str] = {}
    for name, port in NODE_PORTS.items():
        env = os.environ.get(f"GATEWAY_{name}_URL")
        if env:
            urls[name] = env.rstrip("/")
        else:
            urls[name] = os.environ.get(f"{name}_URL", f"http://127.0.0.1:{port}").rstrip("/")
    return urls


def use_real_gateway_protocol() -> bool:
    """True when at least one GATEWAY_*_URL is configured."""
    return any(os.environ.get(f"GATEWAY_{n}_URL") for n in NODE_PORTS)


def next_query_id() -> str:
    return f"q-{next(_query_seq)}"


def _normalize_modality(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    key = str(raw).strip().upper().replace("-", "").replace(" ", "")
    return _MOD_ALIASES.get(key) or (key if key in VOCAB["modalities"]["canonical"] else None)


def _normalize_body_parts(raw: Any) -> list[str]:
    """Accept body_parts list or legacy body_part string → canonical list."""
    items: list[Any]
    if raw is None or raw == "":
        items = []
    elif isinstance(raw, list):
        items = raw
    else:
        items = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item is None or item == "":
            continue
        key = str(item).strip().upper().replace("-", "_")
        canon = _BODY_ALIASES.get(key) or _BODY_ALIASES.get(key.replace("_", ""))
        if not canon and key in VOCAB["body_parts"]["canonical"]:
            canon = key
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def _concept_code(raw: str) -> str:
    """Normalize a free-text concept into an uppercase gateway code."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", raw.strip()).strip("_").upper()
    if cleaned in _CONCEPTS:
        return cleaned
    # fuzzy: match vocab by removing underscores
    compact = cleaned.replace("_", "")
    for code in _CONCEPTS:
        if code.replace("_", "") == compact:
            return code
    return cleaned


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_gestational_weeks(q: str) -> tuple[int | None, int | None]:
    """Extract gestational age weeks from phrases like '20-24 weeks' or '22 weeks'."""
    range_match = re.search(
        r"(\d{1,2})\s*[-–to]+\s*(\d{1,2})\s*(?:weeks?|wks?|ga)\b",
        q,
        re.IGNORECASE,
    )
    if range_match:
        lo, hi = int(range_match.group(1)), int(range_match.group(2))
        return (min(lo, hi), max(lo, hi))
    single = re.search(r"\b(?:ga|gestational(?:\s+age)?)\s*[:=]?\s*(\d{1,2})\b", q, re.IGNORECASE)
    if single:
        w = int(single.group(1))
        return (w, w)
    weeks = re.search(r"\b(\d{1,2})\s*(?:weeks?|wks?)\b", q, re.IGNORECASE)
    if weeks and re.search(r"gestational|fetal|foetal|fetus|ga\b", q, re.IGNORECASE):
        w = int(weeks.group(1))
        return (w, w)
    return (None, None)


def _match_concepts(q: str) -> list[dict[str, str]]:
    """Find known concept codes in the query (longest match first)."""
    upper = q.upper()
    # Also search underscore-as-space forms
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for code in sorted(_CONCEPTS, key=len, reverse=True):
        variants = {
            code,
            code.replace("_", " "),
            code.replace("_", "-"),
        }
        if any(v in upper for v in variants):
            if code not in seen:
                seen.add(code)
                found.append({"code": code, "assertion": "PRESENT"})
    if found:
        return found
    # Fall back: first non-structural token mapped via _concept_code
    structural = {
        "pediatric", "paediatric", "child", "neonatal", "gestational", "age",
        "weeks", "week", "wks", "ga", "magnetic", "resonance", "ultrasound",
        "xray", "with", "without", "and", "the", "for", "of",
    }
    for raw in re.findall(r"[A-Za-z][A-Za-z\-]+", q):
        if raw.lower() in structural or len(raw) < 3:
            continue
        code = _concept_code(raw)
        if code in _CONCEPTS:
            return [{"code": code, "assertion": "PRESENT"}]
    return []


def _match_body_parts(q: str, tokens: set[str]) -> list[str]:
    parts: list[str] = []
    seen: set[str] = set()
    # Alias scan on expanded tokens
    for tok in tokens:
        canon = _BODY_ALIASES.get(tok.upper())
        if canon and canon not in seen:
            seen.add(canon)
            parts.append(canon)
    # Phrase aliases in raw query
    upper = q.upper()
    for alias, canon in _BODY_ALIASES.items():
        if alias in upper and canon not in seen:
            seen.add(canon)
            parts.append(canon)
    return parts


def _match_modality(tokens: set[str], q: str) -> str | None:
    upper_tokens = {t.upper().replace("-", "") for t in tokens}
    for alias, canon in _MOD_ALIASES.items():
        if alias.replace("-", "") in upper_tokens:
            return canon
    if "MAGNETIC" in upper_tokens and "RESONANCE" in upper_tokens:
        return "MR"
    return None


def nl_to_filters(q: str) -> dict[str, Any]:
    """Map natural language (+ vocab) into teammate gateway filters."""
    terms = [t.lower() for t in expand(q)]
    joined = " ".join(terms)
    tokens = set(re.findall(r"[a-z0-9]+", joined))

    patient_age_min: int | None = None
    patient_age_max: int | None = None
    if tokens & {"pediatric", "paediatric", "child", "neonatal"}:
        patient_age_min, patient_age_max = 0, 21

    ga_min, ga_max = _parse_gestational_weeks(q)
    body_parts = _match_body_parts(q, tokens)
    if ga_min is not None and "FETAL" not in body_parts:
        body_parts = ["FETAL", *body_parts]

    modality = _match_modality(tokens, q)
    concepts = _match_concepts(q)

    return {
        "patient_age_min": patient_age_min,
        "patient_age_max": patient_age_max,
        "gestational_age_min_weeks": ga_min,
        "gestational_age_max_weeks": ga_max,
        "modality": modality,
        "body_parts": body_parts,
        "concepts": concepts,
    }


def build_gateway_request(q: str, query_id: str | None = None) -> dict[str, Any]:
    return {
        "query_id": query_id or next_query_id(),
        "filters": nl_to_filters(q),
    }


def normalize_concepts(raw: Any) -> list[dict[str, str]]:
    """Accept concepts[] or legacy concept string from UI/API."""
    if isinstance(raw, list):
        out: list[dict[str, str]] = []
        for item in raw:
            if isinstance(item, dict) and item.get("code"):
                assertion = str(item.get("assertion") or "PRESENT").upper()
                if assertion not in _ASSERTIONS:
                    assertion = "PRESENT"
                out.append({"code": _concept_code(str(item["code"])), "assertion": assertion})
            elif isinstance(item, str) and item.strip():
                out.append({"code": _concept_code(item), "assertion": "PRESENT"})
        return out
    if isinstance(raw, str) and raw.strip():
        return [{"code": _concept_code(raw), "assertion": "PRESENT"}]
    return []


def build_gateway_request_from_filters(
    filters: dict[str, Any],
    query_id: str | None = None,
) -> dict[str, Any]:
    """Build teammate gateway payload from structured UI filters."""
    patient_min = filters.get("patient_age_min", filters.get("age_min"))
    patient_max = filters.get("patient_age_max", filters.get("age_max"))
    concepts = normalize_concepts(filters.get("concepts", filters.get("concept")))
    body_raw = filters.get("body_parts", filters.get("body_part"))

    cleaned: dict[str, Any] = {
        "patient_age_min": _optional_int(patient_min),
        "patient_age_max": _optional_int(patient_max),
        "gestational_age_min_weeks": _optional_int(filters.get("gestational_age_min_weeks")),
        "gestational_age_max_weeks": _optional_int(filters.get("gestational_age_max_weeks")),
        "modality": _normalize_modality(filters.get("modality")),
        "body_parts": _normalize_body_parts(body_raw),
        "concepts": concepts,
    }
    return {
        "query_id": query_id or next_query_id(),
        "filters": cleaned,
    }


def filters_to_nl(filters: dict[str, Any]) -> str:
    """Rebuild a local-node NL query from teammate filters."""
    parts: list[str] = []
    pmax = filters.get("patient_age_max")
    if pmax is not None and _optional_int(pmax) is not None and int(pmax) <= 21:
        parts.append("pediatric")
    ga_min = filters.get("gestational_age_min_weeks")
    ga_max = filters.get("gestational_age_max_weeks")
    if ga_min is not None or ga_max is not None:
        lo = ga_min if ga_min is not None else ga_max
        hi = ga_max if ga_max is not None else ga_min
        if lo == hi:
            parts.append(f"{lo} weeks gestational")
        else:
            parts.append(f"{lo}-{hi} weeks gestational")
    for bp in _normalize_body_parts(filters.get("body_parts", filters.get("body_part"))):
        parts.append(bp.lower())
    mod = _normalize_modality(filters.get("modality"))
    if mod == "MR":
        parts.append("MRI")
    elif mod:
        parts.append(mod)
    for c in normalize_concepts(filters.get("concepts", filters.get("concept"))):
        parts.append(c["code"].replace("_", " ").lower())
    return " ".join(parts) or "brain"


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

        filters = gateway_req.get("filters") or {}
        q = filters_to_nl(filters)

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
