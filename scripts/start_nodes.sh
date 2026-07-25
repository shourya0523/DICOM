#!/usr/bin/env bash
# Start all three hospital nodes with distinct JWT secrets.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Repo root for `shared`; the node's own modules come from --app-dir.
export PYTHONPATH="$ROOT"

NODE_DIR="$ROOT/services/hospital-node"

pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "uvicorn portal.app:app" 2>/dev/null || true
sleep 0.5

HOSPITAL_NODE=BCH NODE_JWT_SECRET="bch-demo-secret-do-not-reuse-32b!" \
  uvicorn main:app --app-dir "$NODE_DIR" --port 8001 --reload &
HOSPITAL_NODE=MGH NODE_JWT_SECRET="mgh-demo-secret-distinct-key-32b!" \
  uvicorn main:app --app-dir "$NODE_DIR" --port 8002 --reload &
HOSPITAL_NODE=BWH NODE_JWT_SECRET="bwh-demo-secret-another-one-32b!" \
  uvicorn main:app --app-dir "$NODE_DIR" --port 8003 --reload &

echo "Nodes starting on 8001/8002/8003 (BCH/MGH/BWH). PIDs: $!"
wait
