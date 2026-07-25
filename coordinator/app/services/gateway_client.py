"""Gateway client — the coordinator's only path to the hospital network.

Fans resolved filters out to each provider gateway's POST /search with the
shared service API key. Falls back to a clear error payload per hospital when
a gateway URL is missing or the call fails.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from config import Config


def to_gateway_filters(resolved: dict[str, Any] | None) -> dict[str, Any]:
    """Map coordinator inline filters → provider-gateway SearchFilters."""
    resolved = resolved or {}
    modalities = resolved.get("modalities")
    if modalities is None:
        modalities = resolved.get("modality") or []
    body_parts = resolved.get("body_parts")
    if body_parts is None:
        body_parts = resolved.get("body_part") or []

    concepts_in = resolved.get("concepts") or []
    concepts: list[dict[str, str]] = []
    for entry in concepts_in:
        if not isinstance(entry, dict):
            continue
        code = entry.get("code")
        if not code:
            continue
        concepts.append(
            {
                "code": str(code).upper(),
                "assertion": str(entry.get("assertion") or "PRESENT").upper(),
            }
        )

    filters: dict[str, Any] = {
        "modalities": list(modalities) if isinstance(modalities, list) else ([modalities] if modalities else []),
        "body_parts": list(body_parts) if isinstance(body_parts, list) else ([body_parts] if body_parts else []),
        "concepts": concepts,
    }
    age_min = resolved.get("patient_age_min", resolved.get("age_min"))
    age_max = resolved.get("patient_age_max", resolved.get("age_max"))
    if age_min is not None:
        filters["age_min"] = age_min
    if age_max is not None:
        filters["age_max"] = age_max
    sex = resolved.get("sex")
    if sex:
        filters["sex"] = sex
    return filters


def _fan_out(fn, hospital_codes: list[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=max(1, len(hospital_codes))) as pool:
        return list(pool.map(fn, hospital_codes))


def _gateway_url(hospital_code: str) -> str | None:
    urls = getattr(Config, "GATEWAY_URLS", {}) or {}
    url = urls.get(hospital_code.upper())
    if url:
        return url.rstrip("/")
    # Single-base fallback (legacy): only useful when one gateway is configured.
    base = (Config.GATEWAY_BASE_URL or "").rstrip("/")
    if base and hospital_code.upper() == (Config.HOSPITAL_CODES or ["BCH"])[0]:
        return base
    return None


def search_one_gateway(
    hospital_code: str,
    filters: dict[str, Any],
    *,
    query_id: str | None = None,
) -> dict[str, Any]:
    """POST /search on one provider gateway."""
    code = hospital_code.upper()
    base = _gateway_url(code)
    if not base:
        return {
            "hospital_code": code,
            "provider": code,
            "status": "unavailable",
            "detail": f"no gateway URL configured for {code}",
            "match_count": None,
            "count_band": None,
            "access_available": False,
        }

    payload = {
        "query_id": query_id or f"coord-{code}",
        "filters": to_gateway_filters(filters),
        "freeze_cohort": True,
    }
    headers = {"Content-Type": "application/json"}
    api_key = getattr(Config, "SERVICE_API_KEY", "") or ""
    if api_key:
        headers["X-API-Key"] = api_key

    try:
        response = requests.post(
            f"{base}/search",
            json=payload,
            headers=headers,
            timeout=Config.GATEWAY_TIMEOUT,
        )
        if response.status_code == 401:
            return {
                "hospital_code": code,
                "provider": code,
                "status": "unauthorized",
                "detail": "gateway rejected SERVICE_API_KEY",
                "match_count": None,
                "count_band": None,
                "access_available": False,
            }
        response.raise_for_status()
        body = response.json()
        return {
            "hospital_code": code,
            "provider": body.get("provider", code),
            "status": "ok",
            "query_id": body.get("query_id"),
            "match_count": body.get("match_count"),
            "count_band": body.get("count_band"),
            "modalities": body.get("modalities") or [],
            "body_parts": body.get("body_parts") or [],
            "cohort_handle": body.get("cohort_handle"),
            "access_available": bool(body.get("access_available", True)),
            "index_timestamp": body.get("index_timestamp"),
            "detail": None,
        }
    except requests.Timeout:
        return {
            "hospital_code": code,
            "provider": code,
            "status": "timeout",
            "detail": f"gateway timeout after {Config.GATEWAY_TIMEOUT}s",
            "match_count": None,
            "count_band": None,
            "access_available": False,
        }
    except requests.RequestException as exc:
        detail = str(exc)
        try:
            if exc.response is not None:
                detail = exc.response.text[:300] or detail
        except Exception:
            pass
        return {
            "hospital_code": code,
            "provider": code,
            "status": "error",
            "detail": detail,
            "match_count": None,
            "count_band": None,
            "access_available": False,
        }


def search_network(
    filters: dict,
    hospital_codes: list[str] | None = None,
    *,
    query_id: str | None = None,
) -> list[dict]:
    """Fan the resolved query out across all hospital codes via their gateways."""
    codes = hospital_codes or Config.HOSPITAL_CODES
    return _fan_out(
        lambda code: search_one_gateway(code, filters, query_id=query_id),
        codes,
    )


# Backwards-compatible names used by older docs / experiments.
def gateway_endpoint_1(hospital_code: str, filters: dict) -> dict:
    return search_one_gateway(hospital_code, filters)


def gateway_endpoint_2(hospital_code: str, payload: dict | None = None) -> dict:
    return {
        "hospital_code": hospital_code,
        "status": "unsupported",
        "endpoint": "endpoint_2",
        "detail": "use POST /search via search_network",
    }


def gateway_endpoint_3(hospital_code: str, payload: dict | None = None) -> dict:
    return {
        "hospital_code": hospital_code,
        "status": "unsupported",
        "endpoint": "endpoint_3",
        "detail": "use POST /search via search_network",
    }
