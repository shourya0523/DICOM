#!/usr/bin/env bash
# Start the local demo without Docker: one hospital node + its provider gateway.
#
#   ./tools/run_local_demo.sh            # BCH: node :8001, gateway :8101
#   PROVIDER_CODE=MGH ./tools/run_local_demo.sh
#
# Ctrl+C stops both processes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

PROVIDER_CODE="${PROVIDER_CODE:-BCH}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

case "$PROVIDER_CODE" in
  BCH) DEFAULT_NODE_PORT=8001; DEFAULT_GATEWAY_PORT=8101; DEFAULT_NAME="Boston Children's Hospital" ;;
  MGH) DEFAULT_NODE_PORT=8002; DEFAULT_GATEWAY_PORT=8102; DEFAULT_NAME="Massachusetts General Hospital" ;;
  BWH) DEFAULT_NODE_PORT=8003; DEFAULT_GATEWAY_PORT=8103; DEFAULT_NAME="Brigham and Women's Hospital" ;;
  *) echo "Unknown PROVIDER_CODE '$PROVIDER_CODE' (expected BCH|MGH|BWH)" >&2; exit 1 ;;
esac

NODE_PORT="${NODE_PORT:-$DEFAULT_NODE_PORT}"
GATEWAY_PORT="${GATEWAY_PORT:-$DEFAULT_GATEWAY_PORT}"
PROVIDER_NAME="${PROVIDER_NAME:-$DEFAULT_NAME}"
TOKEN_SECRET="${TOKEN_SECRET:-local-secret}"
SERVICE_API_KEY="${SERVICE_API_KEY:-demo-key}"
OPENMED_FORCE_FALLBACK="${OPENMED_FORCE_FALLBACK:-1}"

export PYTHONPATH="$REPO_ROOT/services/provider-gateway${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p data/gateway

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "Starting hospital node $PROVIDER_CODE on :$NODE_PORT"
HOSPITAL_NODE="$PROVIDER_CODE" \
  "$PYTHON_BIN" -m uvicorn main:app \
  --app-dir "$REPO_ROOT/services/hospital-node" \
  --host 127.0.0.1 --port "$NODE_PORT" &
pids+=($!)

for _ in $(seq 1 40); do
  if curl -sf "http://localhost:$NODE_PORT/health" >/dev/null; then break; fi
  sleep 0.25
done

echo "Starting provider gateway $PROVIDER_CODE on :$GATEWAY_PORT"
PROVIDER_CODE="$PROVIDER_CODE" \
PROVIDER_NAME="$PROVIDER_NAME" \
NODE_URL="http://localhost:$NODE_PORT" \
DATABASE_PATH="$REPO_ROOT/data/gateway/$(echo "$PROVIDER_CODE" | tr '[:upper:]' '[:lower:]')_gateway.db" \
TOKEN_SECRET="$TOKEN_SECRET" \
SERVICE_API_KEY="$SERVICE_API_KEY" \
GATEWAY_HOST=127.0.0.1 \
GATEWAY_PORT="$GATEWAY_PORT" \
OPENMED_FORCE_FALLBACK="$OPENMED_FORCE_FALLBACK" \
  "$PYTHON_BIN" -m provider_gateway &
pids+=($!)

cat <<EOF

Demo up:
  hospital node     http://localhost:$NODE_PORT/docs
  provider gateway  http://localhost:$GATEWAY_PORT/health  (coordinator entry point)

Build the gateway index:
  curl -X POST http://localhost:$GATEWAY_PORT/refresh -H "X-API-Key: $SERVICE_API_KEY"

Ctrl+C to stop.
EOF

wait
