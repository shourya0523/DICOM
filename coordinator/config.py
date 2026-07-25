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
    HOSPITAL_CODES = [c.strip() for c in _env("HOSPITAL_CODES", "BCH,MGH,BWH").split(",")]

    # --- Gateway (secure access layer in front of the nodes) ---
    # The gateway exposes three endpoints. Real links are TBD — these are stubs
    # to be filled once the gateway service URLs are provided.
    GATEWAY_BASE_URL = _env("GATEWAY_BASE_URL", "http://localhost:6000")
    GATEWAY_ENDPOINTS = {
        # NOTE: purposes/paths are placeholders pending the real gateway links.
        "endpoint_1": _env("GATEWAY_ENDPOINT_1", "/endpoint-1"),
        "endpoint_2": _env("GATEWAY_ENDPOINT_2", "/endpoint-2"),
        "endpoint_3": _env("GATEWAY_ENDPOINT_3", "/endpoint-3"),
    }
    GATEWAY_TIMEOUT = float(_env("GATEWAY_TIMEOUT", "5"))

    # Legacy dev utility: direct node URLs, used only by GET /api/nodes health
    # check. The real search path goes through the gateway, not these.
    HOSPITAL_NODES = {
        "BCH": _env("NODE_BCH_URL", "http://localhost:8001"),
        "MGH": _env("NODE_MGH_URL", "http://localhost:8002"),
        "BWH": _env("NODE_BWH_URL", "http://localhost:8003"),
    }
    NODE_TIMEOUT = float(_env("NODE_TIMEOUT", "5"))
