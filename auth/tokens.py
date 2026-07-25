"""Per-node JWT issue/verify (HS256, node-specific secret)."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

import jwt

from shared.contracts import TOKEN_TTL_SECONDS

_DEFAULT_SECRETS = {
    "BCH": "bch-demo-secret-do-not-reuse-32b!",
    "MGH": "mgh-demo-secret-distinct-key-32b!",
    "BWH": "bwh-demo-secret-another-one-32b!",
}


def resolve_secret(node: str) -> str:
    """Resolve JWT secret for this node. Env NODE_JWT_SECRET wins; else node default."""
    env = os.environ.get("NODE_JWT_SECRET")
    if env:
        return env
    secret = _DEFAULT_SECRETS.get(node)
    if not secret:
        raise RuntimeError(f"No JWT secret configured for node {node}")
    return secret


def secret_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[:12]


def assert_distinct_demo_secrets() -> None:
    """Warn if someone accidentally unified demo secrets (startup check)."""
    values = list(_DEFAULT_SECRETS.values())
    if len(set(values)) != len(values):
        raise RuntimeError("Demo JWT secrets must be distinct per node")


def issue_token(
    *,
    node: str,
    sub: str,
    org: str,
    irb_approved: bool,
    scope: list[str],
    ttl_seconds: int = TOKEN_TTL_SECONDS,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "org": org,
        "irb_approved": irb_approved,
        "node": node,
        "scope": scope,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, resolve_secret(node), algorithm="HS256")


def verify_token(token: str, *, node: str) -> dict[str, Any]:
    """Verify JWT with this node's secret. Reject if node claim mismatches."""
    try:
        claims = jwt.decode(
            token,
            resolve_secret(node),
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError("invalid token") from exc

    if claims.get("node") != node:
        raise ValueError("token node claim mismatch")
    return claims
