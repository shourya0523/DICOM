"""Run one Provider Gateway microservice instance."""

from __future__ import annotations

import os

import uvicorn


def gateway_bind() -> tuple[str, int]:
    host = os.environ.get("GATEWAY_HOST", os.environ.get("HOST", "0.0.0.0"))
    port = int(os.environ.get("GATEWAY_PORT", os.environ.get("PORT", "8101")))
    return host, port


def main() -> None:
    host, port = gateway_bind()
    uvicorn.run(
        "provider_gateway.app:app",
        host=host,
        port=port,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
