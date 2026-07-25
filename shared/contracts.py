"""Frozen request/response DTOs for federated DICOM search.

Edit only at kickoff / Checkpoint 1 with all builders present.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

NodeName = Literal["BCH", "MGH", "BWH"]
QueryStatus = Literal["ok", "suppressed", "denied"]
QueryTier = Literal["full_metadata", "count_only", "none"]
AuditAction = Literal["login", "query", "retrieve"]
AuditDecision = Literal["allow", "deny", "suppress"]

TOKEN_TTL_SECONDS = 300
SUPPRESSION_THRESHOLD = 5
SCOPE_QUERY = "imaging:query"
SCOPE_RETRIEVE = "imaging:retrieve"

NODE_PORTS = {"BCH": 8001, "MGH": 8002, "BWH": 8003}
PORTAL_PORT = 8010


class ResearcherProfile(BaseModel):
    researcher_id: str = Field(..., description="Email used for SSO matching")
    org: str = ""
    irb_approved: bool = False


class LoginSuccess(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_TTL_SECONDS
    scope: list[str]
    node: NodeName


class LoginDenied(BaseModel):
    status: Literal["denied_at_sso"] = "denied_at_sso"
    node: NodeName
    detail: str = "email not on node allowlist"


class QueryRequest(BaseModel):
    q: str
    expanded_terms: Optional[list[str]] = None


class QueryResponse(BaseModel):
    node: NodeName
    status: QueryStatus
    tier: QueryTier
    count: Optional[int] = None
    studies: list[dict[str, Any]] = Field(default_factory=list)
    reason: Optional[str] = None


class RetrieveResponse(BaseModel):
    node: NodeName
    study_id: str
    study: Optional[dict[str, Any]] = None
    status: Literal["ok", "denied"] = "ok"
    reason: Optional[str] = None


class GatewayConcept(BaseModel):
    code: str
    assertion: Literal[
        "PRESENT", "NEGATED", "UNCERTAIN", "HISTORICAL", "FAMILY_HISTORY"
    ] = "PRESENT"


class GatewayFilters(BaseModel):
    """Teammate gateway search filters (nullable ages; body_parts list; concepts[])."""

    patient_age_min: Optional[int] = None
    patient_age_max: Optional[int] = None
    gestational_age_min_weeks: Optional[int] = None
    gestational_age_max_weeks: Optional[int] = None
    modality: Optional[str] = None
    body_parts: list[str] = Field(default_factory=list)
    concepts: list[GatewayConcept] = Field(default_factory=list)


class PortalSearchRequest(BaseModel):
    researcher: ResearcherProfile
    q: Optional[str] = None
    filters: Optional[GatewayFilters] = None


class PortalRetrieveRequest(BaseModel):
    node: NodeName
    study_id: str
    researcher: ResearcherProfile


class AuditEvent(BaseModel):
    ts: str
    researcher_id: str
    action: AuditAction
    decision: AuditDecision
    reason: str
    detail: Optional[str] = None


# Demo researcher profiles (portal UI presets)
DEMO_PROFILES: dict[str, ResearcherProfile] = {
    "harvard_irb": ResearcherProfile(
        researcher_id="jorgenson@harvard.edu",
        org="Harvard University",
        irb_approved=True,
    ),
    "mit_partner": ResearcherProfile(
        researcher_id="lee@mit.edu",
        org="MIT",
        irb_approved=False,
    ),
    "neu": ResearcherProfile(
        researcher_id="patel@northeastern.edu",
        org="Northeastern University",
        irb_approved=False,
    ),
    "bu": ResearcherProfile(
        researcher_id="chen@bu.edu",
        org="Boston University",
        irb_approved=False,
    ),
    "guest": ResearcherProfile(
        researcher_id="guest@example.com",
        org="Public",
        irb_approved=False,
    ),
}
