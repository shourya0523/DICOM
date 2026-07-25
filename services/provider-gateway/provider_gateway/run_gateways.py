#!/usr/bin/env python3
"""Launch three Provider Gateway processes (BCH/MGH/BWH)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]

GATEWAYS = {
    "BCH": {"gateway_port": 8101, "node_port": 8001, "name": "Boston Children's Hospital"},
    "MGH": {"gateway_port": 8102, "node_port": 8002, "name": "Massachusetts General Hospital"},
    "BWH": {"gateway_port": 8103, "node_port": 8003, "name": "Brigham and Women's Hospital"},
}


def main() -> int:
    processes: list[subprocess.Popen] = []
    try:
        for code, cfg in GATEWAYS.items():
            env = os.environ.copy()
            env.update(
                {
                    "PROVIDER_CODE": code,
                    "PROVIDER_NAME": cfg["name"],
                    "NODE_URL": f"http://localhost:{cfg['node_port']}",
                    "DATABASE_PATH": str(ROOT / "data" / "gateway" / f"{code.lower()}_gateway.db"),
                    "TOKEN_SECRET": env.get("TOKEN_SECRET", "local-secret"),
                    "SERVICE_API_KEY": env.get("SERVICE_API_KEY", "demo-key"),
                    "PYTHONPATH": str(PACKAGE_ROOT),
                }
            )
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "provider_gateway.app:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(cfg["gateway_port"]),
                    ],
                    cwd=str(ROOT),
                    env=env,
                )
            )
            print(f"Started {code} gateway on :{cfg['gateway_port']} -> node :{cfg['node_port']}")
        print("Press Ctrl+C to stop.")
        for process in processes:
            process.wait()
    except KeyboardInterrupt:
        print("\nStopping gateways...")
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
