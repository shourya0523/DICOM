"""Per-node SSO allowlists, scopes, and query tiers."""

from __future__ import annotations

from shared.contracts import SCOPE_QUERY, SCOPE_RETRIEVE, SUPPRESSION_THRESHOLD

ALLOWED_EDU_EMAILS: dict[str, list[str]] = {
    "BCH": [
        "jorgenson@harvard.edu",
        "lee@mit.edu",
        "patel@northeastern.edu",
        "chen@bu.edu",
    ],
    "MGH": [
        "jorgenson@harvard.edu",
        "lee@mit.edu",
        "patel@northeastern.edu",
        "chen@bu.edu",
    ],
    "BWH": [
        "lee@mit.edu",
        "patel@northeastern.edu",
        "chen@bu.edu",
        # harvard.edu intentionally omitted → jorgenson denied at BWH SSO
    ],
}

# Nodes that may grant imaging:retrieve when irb_approved is true
RETRIEVE_ALLOWED_NODES: set[str] = {"BCH"}

# Nodes that may return full_metadata when irb_approved is true
FULL_METADATA_NODES: set[str] = {"BCH"}


def is_email_allowed(node: str, email: str) -> bool:
    return email.strip().lower() in {
        e.lower() for e in ALLOWED_EDU_EMAILS.get(node, [])
    }


def scopes_for(node: str, *, irb_approved: bool) -> list[str]:
    scopes = [SCOPE_QUERY]
    if irb_approved and node in RETRIEVE_ALLOWED_NODES:
        scopes.append(SCOPE_RETRIEVE)
    return scopes


def query_tier_for(node: str, *, irb_approved: bool) -> str:
    if irb_approved and node in FULL_METADATA_NODES:
        return "full_metadata"
    return "count_only"


def suppression_threshold() -> int:
    return SUPPRESSION_THRESHOLD
