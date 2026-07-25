"""Gateway client — the coordinator's only path to the hospital network.

The coordinator does NOT call node database ports directly. It iterates over the
configured hospital codes and routes every call through the gateway service,
which exposes three endpoints (secure access layer in front of the nodes).

STATUS: STUBBED. The three real gateway URLs are TBD. Each endpoint below has a
placeholder response and a commented-out real call showing exactly where the
live request goes once the links are provided.
"""
from concurrent.futures import ThreadPoolExecutor

import requests  # noqa: F401  (used once real gateway calls are wired in)

from config import Config


def _fan_out(fn, hospital_codes):
    with ThreadPoolExecutor(max_workers=max(1, len(hospital_codes))) as pool:
        return list(pool.map(fn, hospital_codes))


# --- Endpoint 1 (STUB) — purpose TBD (e.g. query / search) ------------------
def gateway_endpoint_1(hospital_code: str, filters: dict) -> dict:
    """Route a resolved-filter query for one hospital through the gateway.

    TODO: replace stub with the real call once the link is provided, e.g.:
        url = f"{Config.GATEWAY_BASE_URL}{Config.GATEWAY_ENDPOINTS['endpoint_1']}"
        r = requests.post(url, json={"hospital_code": hospital_code, "filters": filters},
                          timeout=Config.GATEWAY_TIMEOUT)
        r.raise_for_status()
        return r.json()
    """
    return {
        "hospital_code": hospital_code,
        "status": "stub",
        "endpoint": "endpoint_1",
        "detail": "gateway endpoint_1 not yet wired — awaiting real URL",
        "echo_filters": filters,
    }


# --- Endpoint 2 (STUB) — purpose TBD ----------------------------------------
def gateway_endpoint_2(hospital_code: str, payload: dict | None = None) -> dict:
    """TODO: wire to Config.GATEWAY_ENDPOINTS['endpoint_2'] when link provided."""
    return {
        "hospital_code": hospital_code,
        "status": "stub",
        "endpoint": "endpoint_2",
        "detail": "gateway endpoint_2 not yet wired — awaiting real URL",
    }


# --- Endpoint 3 (STUB) — purpose TBD ----------------------------------------
def gateway_endpoint_3(hospital_code: str, payload: dict | None = None) -> dict:
    """TODO: wire to Config.GATEWAY_ENDPOINTS['endpoint_3'] when link provided."""
    return {
        "hospital_code": hospital_code,
        "status": "stub",
        "endpoint": "endpoint_3",
        "detail": "gateway endpoint_3 not yet wired — awaiting real URL",
    }


def search_network(filters: dict, hospital_codes: list[str] | None = None) -> list[dict]:
    """Fan the resolved query out across all hospital codes via the gateway."""
    codes = hospital_codes or Config.HOSPITAL_CODES
    return _fan_out(lambda code: gateway_endpoint_1(code, filters), codes)
