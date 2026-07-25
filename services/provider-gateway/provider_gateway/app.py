"""FastAPI Provider Gateway application factory and routes."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from provider_gateway import PIPELINE_VERSION, __version__
from provider_gateway.approvals import ApprovalService
from provider_gateway.cohorts import CohortService
from provider_gateway.config import (
    AGE_BUCKETS,
    ALLOWED_DATA_FIELDS,
    ALLOWED_METADATA_FIELDS,
    CONCEPT_VOCAB,
    Settings,
)
from provider_gateway.datasets import DatasetService
from provider_gateway.hospital_client import HospitalClient, HospitalNodeError
from provider_gateway.models import (
    AccessDecisionRequest,
    AccessRequest,
    AccessRequestCreate,
    AccessRequestStatus,
    AdminMetaResponse,
    CanonicalSearchQuery,
    CapabilitiesResponse,
    HealthResponse,
    OrganisationPolicy,
    OrganisationPolicyCreate,
    OrganisationPolicyUpdate,
    OrgStatus,
    RefreshResponse,
    SearchAggregateResponse,
)
from provider_gateway.openmed_adapter import OpenMedAdapter
from provider_gateway.pipeline import IngestionPipeline
from provider_gateway.repository import build_repository
from provider_gateway.search import SearchService, aggregate

logger = logging.getLogger("provider_gateway")
logging.basicConfig(level=logging.INFO, format="%(message)s")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_settings() -> Settings:
    return Settings.from_env()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = build_repository(settings)
    hospital_client = HospitalClient(
        settings.node_url,
        settings.http_timeout_seconds,
        service_account=settings.node_service_account,
    )
    openmed_adapter = OpenMedAdapter()
    pipeline = IngestionPipeline(settings, repository, hospital_client, openmed_adapter)
    search_service = SearchService(repository)
    cohort_service = CohortService(settings, repository)
    approval_service = ApprovalService(repository)
    dataset_service = DatasetService(repository)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "GATEWAY_STARTED provider=%s node=%s",
            settings.provider_code,
            settings.node_url,
        )
        repository.log_audit(
            "GATEWAY_STARTED",
            {"provider": settings.provider_code, "node_url": settings.node_url},
        )
        yield

    app = FastAPI(
        title=f"Provider Gateway — {settings.provider_code}",
        description="Hospital-controlled boundary for federated medical imaging search.",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = settings
    app.state.repository = repository
    app.state.hospital_client = hospital_client
    app.state.pipeline = pipeline
    app.state.search_service = search_service
    app.state.cohort_service = cohort_service
    app.state.approval_service = approval_service
    app.state.dataset_service = dataset_service

    ingest_router = APIRouter(tags=["ingest"])
    search_router = APIRouter(tags=["search"])
    access_router = APIRouter(tags=["access"])
    admin_router = APIRouter(prefix="/admin", tags=["hospital-admin"])

    def require_api_key(
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> None:
        if x_api_key != settings.service_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    def _validate_org_fields(policy: OrganisationPolicyCreate | OrganisationPolicy) -> None:
        global_meta = set(ALLOWED_METADATA_FIELDS)
        global_data = set(ALLOWED_DATA_FIELDS)
        if not set(policy.allowed_metadata_fields).issubset(global_meta):
            raise HTTPException(
                status_code=400,
                detail="allowed_metadata_fields contains values outside hospital catalog",
            )
        if not set(policy.allowed_data_fields).issubset(global_data):
            raise HTTPException(
                status_code=400,
                detail="allowed_data_fields contains values outside hospital catalog",
            )

    def _fulfill_approved(request: AccessRequest) -> AccessRequest:
        cohort = cohort_service.get(request.cohort_handle)
        request = approval_service.transition(
            request,
            AccessRequestStatus.GENERATING_DATASET,
            "Generating de-identified dataset preview",
        )
        try:
            if cohort is None:
                raise ValueError("Cohort not found")
            preview = dataset_service.generate_preview(
                provider=settings.provider_code,
                cohort=cohort,
            )
            request.dataset_id = preview.dataset_id
            request.dataset_preview = preview
            return approval_service.transition(
                request,
                AccessRequestStatus.DELIVERY_READY,
                "Dataset preview ready",
            )
        except Exception:
            return approval_service.transition(
                request,
                AccessRequestStatus.GENERATION_FAILED,
                "Dataset generation failed",
            )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        node_reachable = None
        try:
            hospital_client.health()
            node_reachable = True
        except HospitalNodeError:
            node_reachable = False
        return HealthResponse(
            status="healthy",
            provider=settings.provider_code,
            provider_name=settings.provider_name,
            node_reachable=node_reachable,
            indexed_studies=repository.count_evidence(),
        )

    @app.get("/capabilities", response_model=CapabilitiesResponse)
    def capabilities(
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> CapabilitiesResponse:
        return CapabilitiesResponse(
            provider=settings.provider_code,
            provider_name=settings.provider_name,
            pipeline_version=PIPELINE_VERSION,
            concepts=sorted(CONCEPT_VOCAB.keys()),
            age_buckets=list(AGE_BUCKETS),
            allowed_metadata_fields=list(ALLOWED_METADATA_FIELDS),
            allowed_data_fields=list(ALLOWED_DATA_FIELDS),
            endpoints=[
                "GET /health",
                "GET /capabilities",
                "POST /refresh",
                "POST /search",
                "POST /access-requests",
                "GET /access-requests/{provider_request_id}",
                "GET /access-requests?coordinator_access_request_id=",
                "GET /admin/meta",
                "GET /admin/organisations",
                "POST /admin/organisations",
                "PUT /admin/organisations/{organisation_id}",
                "DELETE /admin/organisations/{organisation_id}",
                "GET /admin/access-requests",
                "POST /admin/access-requests/{provider_request_id}/approve",
                "POST /admin/access-requests/{provider_request_id}/deny",
            ],
        )

    @ingest_router.post("/refresh", response_model=RefreshResponse)
    def refresh(
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> RefreshResponse:
        return pipeline.refresh(warm_models=True)

    @search_router.post("/search", response_model=SearchAggregateResponse)
    def search(
        query: CanonicalSearchQuery,
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> SearchAggregateResponse:
        unknown = [
            c.code for c in query.filters.concepts if c.code not in CONCEPT_VOCAB
        ]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown concept codes: {unknown}",
            )
        matches, _fp = search_service.execute(query)
        cohort_handle = None
        if query.freeze_cohort:
            cohort = cohort_service.freeze(
                query,
                matches,
                index_version=PIPELINE_VERSION,
            )
            cohort_handle = cohort.cohort_handle
        return aggregate(
            settings.provider_code,
            query,
            matches,
            index_timestamp=repository.get_index_timestamp(),
            access_available=True,
            cohort_handle=cohort_handle,
        )

    @access_router.post("/access-requests", response_model=AccessRequest)
    def create_access_request(
        body: AccessRequestCreate,
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> AccessRequest:
        existing = repository.get_access_request_by_coordinator(
            body.coordinator_access_request_id
        )
        if existing:
            return existing

        request = approval_service.new_request(body)
        request = approval_service.transition(
            request,
            AccessRequestStatus.VALIDATING,
            "Validating cohort and organisation policy",
        )

        cohort = cohort_service.get(body.cohort_handle)
        status, reason, path, approved = approval_service.decide(body, cohort)
        request.approval_path = path
        request.approved_fields = approved
        request = approval_service.transition(request, status, reason)

        if status == AccessRequestStatus.APPROVED:
            logger.info("ACCESS_AUTO_APPROVED id=%s", request.provider_request_id)
            repository.log_audit(
                "ACCESS_AUTO_APPROVED",
                {"provider_request_id": request.provider_request_id},
            )
            request = _fulfill_approved(request)
        elif status == AccessRequestStatus.PENDING_REVIEW:
            logger.info("ACCESS_PENDING_REVIEW id=%s", request.provider_request_id)
            repository.log_audit(
                "ACCESS_PENDING_REVIEW",
                {"provider_request_id": request.provider_request_id},
            )
        elif status in {
            AccessRequestStatus.REJECTED,
            AccessRequestStatus.EXPIRED,
        }:
            logger.info("ACCESS_REJECTED id=%s", request.provider_request_id)
            repository.log_audit(
                "ACCESS_REJECTED",
                {
                    "provider_request_id": request.provider_request_id,
                    "status": status.value,
                },
            )

        return request

    @access_router.get(
        "/access-requests/{provider_request_id}",
        response_model=AccessRequest,
    )
    def get_access_request(
        provider_request_id: str,
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> AccessRequest:
        request = repository.get_access_request(provider_request_id)
        if not request:
            raise HTTPException(status_code=404, detail="Access request not found")
        return request

    @access_router.get("/access-requests", response_model=AccessRequest)
    def get_access_request_by_coordinator(
        coordinator_access_request_id: str = Query(...),
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> AccessRequest:
        request = repository.get_access_request_by_coordinator(
            coordinator_access_request_id
        )
        if not request:
            raise HTTPException(status_code=404, detail="Access request not found")
        return request

    @admin_router.get("/meta", response_model=AdminMetaResponse)
    def admin_meta(
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> AdminMetaResponse:
        return AdminMetaResponse(
            provider=settings.provider_code,
            provider_name=settings.provider_name,
            allowed_metadata_fields=list(ALLOWED_METADATA_FIELDS),
            allowed_data_fields=list(ALLOWED_DATA_FIELDS),
            pending_review_count=repository.count_access_requests(
                AccessRequestStatus.PENDING_REVIEW
            ),
        )

    @admin_router.get("/organisations", response_model=list[OrganisationPolicy])
    def list_organisations(
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> list[OrganisationPolicy]:
        return repository.list_organisations()

    @admin_router.post("/organisations", response_model=OrganisationPolicy)
    def create_organisation(
        body: OrganisationPolicyCreate,
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> OrganisationPolicy:
        if repository.get_organisation(body.organisation_id):
            raise HTTPException(
                status_code=409,
                detail=f"Organisation already exists: {body.organisation_id}",
            )
        _validate_org_fields(body)
        policy = OrganisationPolicy(**body.model_dump())
        saved = repository.save_organisation(policy)
        repository.log_audit(
            "ORG_ALLOWLIST_CREATED",
            {"organisation_id": saved.organisation_id},
        )
        return saved

    @admin_router.put(
        "/organisations/{organisation_id}",
        response_model=OrganisationPolicy,
    )
    def update_organisation(
        organisation_id: str,
        body: OrganisationPolicyUpdate,
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> OrganisationPolicy:
        existing = repository.get_organisation(organisation_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Organisation not found")
        updates = body.model_dump(exclude_unset=True)
        merged = existing.model_copy(update=updates)
        _validate_org_fields(merged)
        saved = repository.save_organisation(merged)
        repository.log_audit(
            "ORG_ALLOWLIST_UPDATED",
            {"organisation_id": organisation_id, "fields": list(updates.keys())},
        )
        return saved

    @admin_router.delete("/organisations/{organisation_id}")
    def delete_organisation(
        organisation_id: str,
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> dict[str, str]:
        existing = repository.get_organisation(organisation_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Organisation not found")
        # Soft-delete via status so historical requests remain interpretable.
        revoked = existing.model_copy(update={"status": OrgStatus.REVOKED})
        repository.save_organisation(revoked)
        repository.log_audit(
            "ORG_ALLOWLIST_REVOKED",
            {"organisation_id": organisation_id},
        )
        return {"status": "revoked", "organisation_id": organisation_id}

    @admin_router.get("/access-requests", response_model=list[AccessRequest])
    def list_admin_access_requests(
        status: AccessRequestStatus | None = Query(None),
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> list[AccessRequest]:
        return repository.list_access_requests(status=status)

    @admin_router.post(
        "/access-requests/{provider_request_id}/approve",
        response_model=AccessRequest,
    )
    def approve_access_request(
        provider_request_id: str,
        body: AccessDecisionRequest | None = None,
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> AccessRequest:
        body = body or AccessDecisionRequest()
        request = repository.get_access_request(provider_request_id)
        if not request:
            raise HTTPException(status_code=404, detail="Access request not found")
        if body.approved_fields is not None:
            allowed = set(ALLOWED_METADATA_FIELDS) | set(ALLOWED_DATA_FIELDS)
            if not set(body.approved_fields).issubset(allowed):
                raise HTTPException(
                    status_code=400,
                    detail="approved_fields contains values outside hospital catalog",
                )
        try:
            request = approval_service.manual_approve(
                request,
                reason=body.reason,
                approved_fields=body.approved_fields,
                actor=body.actor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        logger.info("ACCESS_MANUAL_APPROVED id=%s", request.provider_request_id)
        repository.log_audit(
            "ACCESS_MANUAL_APPROVED",
            {
                "provider_request_id": request.provider_request_id,
                "actor": body.actor,
            },
        )
        return _fulfill_approved(request)

    @admin_router.post(
        "/access-requests/{provider_request_id}/deny",
        response_model=AccessRequest,
    )
    def deny_access_request(
        provider_request_id: str,
        body: AccessDecisionRequest | None = None,
        _: Annotated[None, Depends(require_api_key)] = None,
    ) -> AccessRequest:
        body = body or AccessDecisionRequest()
        request = repository.get_access_request(provider_request_id)
        if not request:
            raise HTTPException(status_code=404, detail="Access request not found")
        try:
            request = approval_service.manual_deny(
                request,
                reason=body.reason,
                actor=body.actor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        logger.info("ACCESS_MANUAL_DENIED id=%s", request.provider_request_id)
        repository.log_audit(
            "ACCESS_MANUAL_DENIED",
            {
                "provider_request_id": request.provider_request_id,
                "actor": body.actor,
            },
        )
        return request

    app.include_router(ingest_router)
    app.include_router(search_router)
    app.include_router(access_router)
    app.include_router(admin_router)

    if STATIC_DIR.is_dir():
        app.mount(
            "/portal/assets",
            StaticFiles(directory=STATIC_DIR),
            name="portal-assets",
        )

        @app.get("/portal", include_in_schema=False)
        @app.get("/portal/", include_in_schema=False)
        def hospital_portal() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
