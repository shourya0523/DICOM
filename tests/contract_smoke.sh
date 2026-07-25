#!/usr/bin/env bash
# Thin wrapper — prefer the Python harness for reliable assertions.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 tests/contract_smoke.py
