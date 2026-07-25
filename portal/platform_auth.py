"""Platform SSO — gate access to the central portal (separate from hospital gateway SSO)."""

from __future__ import annotations

import os
import time
from typing import Any

import jwt
from fastapi import HTTPException
from pydantic import BaseModel, Field

PLATFORM_TOKEN_TTL = 60 * 60 * 8  # 8 hours for demo session
PLATFORM_SECRET = os.environ.get(
    "PLATFORM_JWT_SECRET",
    "platform-sso-secret-distinct-from-nodes!!",
)

# Who may enter the platform (not the same as hospital gateway allowlists)
PLATFORM_ALLOWED_EMAILS = {
    "jorgenson@harvard.edu",
    "lee@mit.edu",
    "patel@northeastern.edu",
    "chen@bu.edu",
}


class PlatformLoginRequest(BaseModel):
    email: str
    org: str = ""
    display_name: str = ""


class PlatformSession(BaseModel):
    email: str
    org: str = ""
    display_name: str = ""
    token: str
    expires_in: int = PLATFORM_TOKEN_TTL


def is_platform_allowed(email: str) -> bool:
    return email.strip().lower() in PLATFORM_ALLOWED_EMAILS


def issue_platform_token(*, email: str, org: str = "", display_name: str = "") -> str:
    now = int(time.time())
    payload = {
        "sub": email.strip().lower(),
        "org": org,
        "display_name": display_name or email,
        "aud": "platform",
        "iat": now,
        "exp": now + PLATFORM_TOKEN_TTL,
    }
    return jwt.encode(payload, PLATFORM_SECRET, algorithm="HS256")


def verify_platform_token(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            PLATFORM_SECRET,
            algorithms=["HS256"],
            audience="platform",
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="platform session expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid platform session") from exc
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="invalid platform session")
    return claims


def login(req: PlatformLoginRequest) -> PlatformSession:
    email = req.email.strip().lower()
    if not is_platform_allowed(email):
        raise HTTPException(
            status_code=403,
            detail="email not authorized for platform SSO",
        )
    token = issue_platform_token(
        email=email,
        org=req.org,
        display_name=req.display_name or email,
    )
    return PlatformSession(
        email=email,
        org=req.org,
        display_name=req.display_name or email,
        token=token,
    )
