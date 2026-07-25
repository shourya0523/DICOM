#!/usr/bin/env bash
# Start the central portal broker on :8010
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Repo root for `shared`; services/portal for the `portal` package.
export PYTHONPATH="$ROOT:$ROOT/services/portal"

uvicorn portal.app:app --port 8010 --reload
