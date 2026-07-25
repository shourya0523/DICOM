#!/usr/bin/env python3
"""CP1/CP2 contract smoke tests for nodes + portal."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BCH = "http://127.0.0.1:8001"
MGH = "http://127.0.0.1:8002"
BWH = "http://127.0.0.1:8003"
PORTAL = "http://127.0.0.1:8010"

HARVARD = {
    "researcher_id": "jorgenson@harvard.edu",
    "org": "Harvard University",
    "irb_approved": True,
}
MIT = {"researcher_id": "lee@mit.edu", "org": "MIT", "irb_approved": False}
GUEST = {"researcher_id": "guest@example.com", "org": "Public", "irb_approved": False}

passed = 0
failed = 0


def check(name: str, cond: bool) -> None:
    global passed, failed
    if cond:
        print(f"PASS  {name}")
        passed += 1
    else:
        print(f"FAIL  {name}")
        failed += 1


def req(method: str, url: str, body: dict | None = None, token: str | None = None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def main() -> int:
    print("=== Health ===")
    for name, url in [("BCH", BCH), ("MGH", MGH), ("BWH", BWH)]:
        code, data = req("GET", f"{url}/health")
        check(f"{name} health", code == 200 and data.get("node") == name)
    code, data = req("GET", f"{PORTAL}/health")
    check("Portal health", code == 200 and data.get("service") == "portal")

    print("=== SSO login ===")
    code, bch_login = req("POST", f"{BCH}/auth/login", HARVARD)
    check("Harvard SSO on BCH", code == 200 and "access_token" in bch_login)
    code, mgh_login = req("POST", f"{MGH}/auth/login", HARVARD)
    check("Harvard SSO on MGH", code == 200 and "access_token" in mgh_login)
    code, _ = req("POST", f"{BWH}/auth/login", HARVARD)
    check("Harvard denied at BWH SSO", code == 403)
    code, _ = req("POST", f"{BCH}/auth/login", GUEST)
    check("Guest denied at BCH SSO", code == 403)

    print("=== Query tiers ===")
    bch_token = bch_login["access_token"]
    mgh_token = mgh_login["access_token"]
    code, bch_q = req("POST", f"{BCH}/query", {"q": "pediatric brain MRI"}, bch_token)
    check("BCH full_metadata tier", code == 200 and bch_q.get("tier") == "full_metadata")
    code, mgh_q = req("POST", f"{MGH}/query", {"q": "pediatric brain MRI"}, mgh_token)
    check("MGH count_only tier", code == 200 and mgh_q.get("tier") == "count_only")
    code, _ = req("POST", f"{MGH}/query", {"q": "brain"}, bch_token)
    check("BCH token rejected by MGH", code == 401)

    print("=== Suppression ===")
    code, supp = req("POST", f"{BCH}/query", {"q": "lissencephaly"}, bch_token)
    check("lissencephaly suppressed on BCH", code == 200 and supp.get("status") == "suppressed")

    print("=== Retrieve scope ===")
    studies = bch_q.get("studies") or []
    study_id = studies[0]["StudyID"] if studies else "BR-1543"
    code, ret = req("GET", f"{BCH}/retrieve/{study_id}", token=bch_token)
    check("Harvard retrieve on BCH", code == 200 and ret.get("status") == "ok")
    code, mit_login = req("POST", f"{BCH}/auth/login", MIT)
    mit_token = mit_login["access_token"]
    code, _ = req("GET", f"{BCH}/retrieve/BR-1543", token=mit_token)
    check("MIT retrieve denied (no scope)", code == 403)

    print("=== Legacy studies gated ===")
    code, _ = req("GET", f"{BCH}/api/studies")
    check("Unauth /api/studies → 401", code == 401)

    print("=== Platform SSO ===")
    code, _ = req(
        "POST",
        f"{PORTAL}/platform/login",
        {"email": "guest@example.com", "org": "Public"},
    )
    check("Guest denied at platform SSO", code == 403)

    code, plat = req(
        "POST",
        f"{PORTAL}/platform/login",
        {"email": HARVARD["researcher_id"], "org": HARVARD["org"]},
    )
    check("Harvard platform SSO", code == 200 and "token" in plat)
    platform_token = plat.get("access_token") or plat.get("token")

    code, _ = req("POST", f"{PORTAL}/search", {"q": "brain", "researcher": HARVARD})
    check("Portal search requires platform token", code == 401)

    print("=== Portal fan-out (via gateway adapter) ===")
    code, portal_search = req(
        "POST",
        f"{PORTAL}/search",
        {"q": "pediatric brain MRI", "researcher": HARVARD},
        platform_token,
    )
    nodes = {n["node"]: n for n in portal_search.get("nodes", [])}
    check("Portal returns BCH node", code == 200 and "BCH" in nodes)
    check("Portal returns BWH deny path", nodes.get("BWH", {}).get("status") == "denied")
    gw_req = portal_search.get("gateway_request") or {}
    check(
        "Gateway request has filters",
        isinstance(gw_req.get("filters"), dict) and "query_id" in gw_req,
    )
    gw_resps = portal_search.get("gateway_responses") or []
    check("Gateway responses present", len(gw_resps) >= 2)
    bch_gw = next((g for g in gw_resps if g.get("provider") == "BCH"), {})
    check(
        "BCH gateway count_band",
        bch_gw.get("status") == "complete" and bool(bch_gw.get("count_band")),
    )

    code, preview = req(
        "POST",
        f"{PORTAL}/gateway/preview",
        {"q": "pediatric brain MRI hydrocephalus"},
        platform_token,
    )
    check(
        "Gateway preview maps NL",
        code == 200
        and (preview.get("gateway_request") or {}).get("filters", {}).get("body_parts") == ["BRAIN"],
    )

    code, structured = req(
        "POST",
        f"{PORTAL}/search",
        {
            "researcher": HARVARD,
            "filters": {
                "patient_age_min": 0,
                "patient_age_max": 21,
                "gestational_age_min_weeks": None,
                "gestational_age_max_weeks": None,
                "modality": "MR",
                "body_parts": ["BRAIN"],
                "concepts": [{"code": "HYDROCEPHALUS", "assertion": "PRESENT"}],
            },
        },
        platform_token,
    )
    s_req = (structured.get("gateway_request") or {}).get("filters") or {}
    concepts = s_req.get("concepts") or []
    check(
        "Structured filters search",
        code == 200
        and s_req.get("modality") == "MR"
        and s_req.get("body_parts") == ["BRAIN"]
        and concepts
        and concepts[0].get("code") == "HYDROCEPHALUS",
    )

    code, fetal = req(
        "POST",
        f"{PORTAL}/gateway/preview",
        {"q": "fetal MRI 20-24 weeks ventriculomegaly"},
        platform_token,
    )
    f_filters = (fetal.get("gateway_request") or {}).get("filters") or {}
    check(
        "Gateway preview maps gestational age",
        code == 200
        and f_filters.get("body_parts") == ["FETAL"]
        and f_filters.get("gestational_age_min_weeks") == 20
        and f_filters.get("gestational_age_max_weeks") == 24
        and (f_filters.get("concepts") or [{}])[0].get("code") == "VENTRICULOMEGALY",
    )

    code, portal_ret = req(
        "POST",
        f"{PORTAL}/retrieve",
        {"node": "BCH", "study_id": "BR-1543", "researcher": HARVARD},
        platform_token,
    )
    check("Portal brokers retrieve", code == 200 and portal_ret.get("study_id") == "BR-1543")
    code, _ = req(
        "POST",
        f"{PORTAL}/retrieve",
        {"node": "BCH", "study_id": "BR-1543", "researcher": MIT},
        platform_token,
    )
    check("Portal retrieve deny for MIT", code == 403)

    print()
    print(f"Results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
