"""HTTP routes for the coordinator API.

Flow for /search:
  1. Receive { query_id, nl-string, filters? } from the frontend.
  2. Ask Gemini to deduce filters from the natural-language string.
  3. Resolve: user-provided filters win; Gemini fills only the gaps; every
     value is validated against the controlled vocab.
  4. Return the resolved filters (so the user sees what we pre-filled/deduced).
  5. Fan the resolved query out across the hospital network via the gateway.
"""
from flask import Blueprint, current_app, jsonify, request

from app.services.filter_resolver import resolve_filters
from app.services.gateway_client import search_network
from app.services.gemini_client import deduce_filters
from app.services.node_client import ping_nodes

bp = Blueprint("api", __name__)


@bp.get("/health")
def health():
    """Liveness check for the coordinator itself."""
    return jsonify(status="healthy", service="coordinator")


@bp.get("/api/nodes")
def nodes():
    """Dev utility: direct reachability check of node ports (not the search path)."""
    return jsonify(nodes=ping_nodes(current_app.config["HOSPITAL_NODES"]))


@bp.post("/search")
def search():
    body = request.get_json(silent=True) or {}

    # nl-string is mandatory; accept both hyphen and underscore spellings.
    nl_string = body.get("nl-string") or body.get("nl_string")
    if not nl_string or not str(nl_string).strip():
        return jsonify(error="'nl-string' is required"), 400

    query_id = body.get("query_id")
    user_filters = body.get("filters") or {}

    # Deduce missing filters from the NL string, then merge (user wins).
    deduced, gemini_meta = deduce_filters(nl_string, user_filters)
    resolved_filters, provenance = resolve_filters(user_filters, deduced)

    # Fan out across the hospital network via each provider gateway.
    results = search_network(
        resolved_filters,
        current_app.config["HOSPITAL_CODES"],
        query_id=query_id,
    )
    return jsonify(
        query_id=query_id,
        nl_string=nl_string,
        resolved_filters=resolved_filters,   # copy back so the user sees what we deduced
        filter_provenance=provenance,        # per-field: "user" | "gemini" | "none"
        gemini=gemini_meta,
        results=results,
    )
