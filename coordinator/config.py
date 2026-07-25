"""Coordinator service configuration.

Values are read from environment variables (optionally via a .env file) so the
same code runs locally and in a container without edits. See .env.example.
"""
import os

from dotenv import load_dotenv

load_dotenv()  # loads .env if present; real env vars still win


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class Config:
    # --- Flask ---
    DEBUG = _env("FLASK_DEBUG", "1") == "1"
    HOST = _env("COORDINATOR_HOST", "0.0.0.0")
    PORT = int(_env("COORDINATOR_PORT", "5001"))

    # CORS — the frontend origin(s) allowed to call this API.
    # Comma-separated list, or "*" to allow all (fine for local dev).
    CORS_ORIGINS = _env("CORS_ORIGINS", "*")

    # --- Gemini (filter deduction) ---
    # Fill GEMINI_API_KEY in your .env; the service degrades gracefully
    # (skips deduction, keeps user filters) when it is unset.
    GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
    GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.0-flash")
    GEMINI_API_BASE = _env(
        "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
    )
    GEMINI_TIMEOUT = float(_env("GEMINI_TIMEOUT", "20"))

    # --- Hospital network ---
    # The coordinator fans queries out over these hospital codes. It does NOT
    # call node database ports directly — every call is routed via the gateway.
    HOSPITAL_CODES = [c.strip() for c in _env("HOSPITAL_CODES", "BCH,MGH,BWH").split(",") if c.strip()]

    # Shared key for authenticated gateway calls (X-API-Key).
    SERVICE_API_KEY = _env("SERVICE_API_KEY", "demo-key")

    # Per-hospital provider gateway base URLs (preferred).
    GATEWAY_URLS = {
        "BCH": _env("GATEWAY_BCH_URL", _env("GATEWAY_BASE_URL", "http://localhost:8101")),
        "MGH": _env("GATEWAY_MGH_URL", "http://localhost:8102"),
        "BWH": _env("GATEWAY_BWH_URL", "http://localhost:8103"),
    }
    # Legacy single-base URL kept for older env files / docs.
    GATEWAY_BASE_URL = _env("GATEWAY_BASE_URL", GATEWAY_URLS["BCH"])
    GATEWAY_ENDPOINTS = {
        "endpoint_1": _env("GATEWAY_ENDPOINT_1", "/search"),
        "endpoint_2": _env("GATEWAY_ENDPOINT_2", "/access-requests"),
        "endpoint_3": _env("GATEWAY_ENDPOINT_3", "/capabilities"),
    }
    GATEWAY_TIMEOUT = float(_env("GATEWAY_TIMEOUT", "30"))

    # Legacy dev utility: direct node URLs, used only by GET /api/nodes health
    # check. The real search path goes through the gateway, not these.
    HOSPITAL_NODES = {
        "BCH": _env("NODE_BCH_URL", "http://localhost:8001"),
        "MGH": _env("NODE_MGH_URL", "http://localhost:8002"),
        "BWH": _env("NODE_BWH_URL", "http://localhost:8003"),
    }
    NODE_TIMEOUT = float(_env("NODE_TIMEOUT", "5"))
