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
| Portal | 8010 | **Platform SSO** + UI; delegates search to coordinator |
| Coordinator | 5001 | NL filter deduction (Gemini) + gateway fan-out |
| Provider gateway | 8101–8103 | Hospital-controlled search boundary |
| BCH / MGH / BWH | 8001–8003 | Local hospital backends |

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

One folder holds the whole local demo; every runnable service lives under `services/`.

```
services/
  hospital-node/       # Hospital edge app
    main.py            #   node API (SSO, /query, /retrieve, /audit)
    models.py          #   StudyRecord schema
    search.py          #   local study matcher
    auth/              #   node SSO / JWT / audit
  portal/portal/       # Researcher portal
    platform_auth.py   #   platform SSO
    gateway.py         #   gateway request/response adapter
    app.py             #   portal API + static dashboard
    static/            #   product UI (/) and lab UI (/lab)
  provider-gateway/
    provider_gateway/  # Installable provider gateway package
coordinator/           # Flask brain: Gemini filters + gateway fan-out (:5001)
shared/                # Frozen cross-service contracts + vocab
data/
  hospitals/           # bch/mgh/bwh_data.json (committed)
  gateway/             # gateway SQLite state (generated, gitignored)
  reports/             # accuracy/benchmark output (generated, gitignored)
scripts/               # start_nodes.sh, start_portal.sh
tests/                 # pytest suite, contract smoke, eval/benchmark
tools/                 # run_local_demo.sh, dev_explorer.py
compose.yml            # one-command local demo stack
Dockerfile             # multi-stage: gateway + hospital-node + portal + coordinator
```

Services import `shared` from the repo root, so run commands from the repo root; the
launcher scripts and images set `PYTHONPATH` themselves.

## Provider gateway + Compose demo

Local microservice packaging (alongside the portal above) — full walkthrough in [DEMO.md](DEMO.md):

```bash
cp .env.example .env                          # set TOKEN_SECRET + SERVICE_API_KEY
docker compose up --build                     # BCH node :8001 + provider gateway :8101
docker compose --profile portal up --build    # + MGH/BWH, coordinator :5001, portal :8010
```

Without Docker: `./tools/run_local_demo.sh` starts the BCH node and its gateway in one terminal.

The coordinator calls the gateway (`:8101` for BCH, `:8102`/`:8103` for MGH/BWH), never the
hospital node. `modalities` and `body_parts` are lists in every gateway request and response.

| Variable | Example | Notes |
| --- | --- | --- |
| `PROVIDER_CODE` / `PROVIDER_NAME` | `BCH` / `Boston Children's Hospital` | |
| `NODE_URL` | `http://localhost:8001` | this provider's hospital node |
| `DATABASE_PATH` | `./data/gateway/bch_gateway.db` | SQLite index |
| `TOKEN_SECRET` / `SERVICE_API_KEY` | — | study-token secret / coordinator shared key |
| `GATEWAY_HOST` / `GATEWAY_PORT` | `0.0.0.0` / `8101` | |
| `OPENMED_FORCE_FALLBACK` | `1` | keyword concepts only; skips the model download |
| `NODE_SERVICE_ID` / `NODE_SERVICE_ORG` / `NODE_SERVICE_IRB_APPROVED` | `jorgenson@harvard.edu` / `Harvard University` / `1` | identity the gateway uses when its node enforces SSO; must be allowlisted and IRB-approved to receive full metadata |

Hospital study JSON lives under `data/hospitals/`; the node reads it from there and Compose
bind-mounts it, so edits need no rebuild (`HOSPITAL_DATA_DIR` overrides the location).
