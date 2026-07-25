"""FastAPI dependencies for Bearer JWT verification."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.tokens import verify_token

_bearer = HTTPBearer(auto_error=False)


def require_token(node: str) -> Callable:
    def _dep(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> dict[str, Any]:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="missing bearer token")
        try:
            return verify_token(credentials.credentials, node=node)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    return _dep


def require_scope(scope: str) -> Callable:
    def _dep(request: Request) -> None:
        claims = getattr(request.state, "claims", None)
        if not claims:
            raise HTTPException(status_code=401, detail="missing claims")
        scopes = claims.get("scope") or []
        if scope not in scopes:
            raise HTTPException(
                status_code=403,
                detail=f"missing required scope: {scope}",
            )

    return _dep
