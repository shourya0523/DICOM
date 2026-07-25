# Federated DICOM Search — Zero-Trust Nodes + Portal

Hackathon Track 1: each hospital is an independent FastAPI node with its own SSO
and policy. A central portal fans out queries and brokers authorized results to
the researcher (`node → portal → user`).

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH="$PWD"

# Terminal 1 — hospital nodes
./scripts/start_nodes.sh

# Terminal 2 — portal UI + API
./scripts/start_portal.sh
```

- Portal UI: http://127.0.0.1:8010 (Warm Sand product UI)
- Lab / test UI: http://127.0.0.1:8010/lab (previous dark dashboard)
- BCH / MGH / BWH: `:8001` / `:8002` / `:8003`
- Contract smoke: `python3 tests/contract_smoke.py`

See [DEMO.md](DEMO.md) for the pitch walkthrough and known gaps.

## Architecture

| Service | Port | Role |
|---------|------|------|
| Portal | 8010 | **Platform SSO** + UI; fans out gateway-shaped searches |
| BCH / MGH / BWH | 8001–8003 | Local hospital backends (mock gateways until `GATEWAY_*_URL` set) |

**Dual SSO:** platform login unlocks the dashboard; each hospital gateway runs its own SSO/PII policy. Set `GATEWAY_BCH_URL` / `GATEWAY_MGH_URL` / `GATEWAY_BWH_URL` to point at real gateways — otherwise local nodes mock that contract.

## Portal API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/platform/login` | Platform SSO → session token |
| GET | `/platform/me` | Current platform session |
| POST | `/search` | Requires platform Bearer; emits gateway `{query_id, filters}` fan-out |
| POST | `/retrieve` | Broker study payload (platform Bearer) |
| GET | `/profiles` | Demo researcher presets |
| GET | `/audit/{node}` | Proxy node audit log |

## Layout

```
auth/              # Hospital-node SSO / JWT / audit (mock gateway backend)
portal/
  platform_auth.py # Platform SSO
  gateway.py       # Teammate gateway request/response adapter
  app.py           # Portal API + static dashboard
  static/          # Dashboard UI (login gate + search cards)
shared/            # Frozen contracts
search.py          # Local study matcher
main.py            # Hospital node app
tests/             # Contract smoke harness
```
