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

- Portal UI: http://127.0.0.1:8010
- BCH / MGH / BWH: `:8001` / `:8002` / `:8003`
- Contract smoke: `python3 tests/contract_smoke.py`

See [DEMO.md](DEMO.md) for the pitch walkthrough and known gaps.

## Architecture

| Service | Port | Role |
|---------|------|------|
| BCH node | 8001 | Pediatric data; Harvard IRB → full metadata + retrieve |
| MGH node | 8002 | Adult data; allowlisted `.edu` → count-only |
| BWH node | 8003 | Adult data; no Harvard on SSO allowlist |
| Portal | 8010 | Synonym expansion, fan-out, aggregate suppression, UI |

SSO: per-node allowlists of `harvard.edu` / `mit.edu` / `northeastern.edu` / `bu.edu` emails.
Tokens are HS256 JWTs signed with **distinct** per-node secrets (5-minute TTL).
Scopes: `imaging:query`, `imaging:retrieve`.

## Node API

| Method | Path | Auth |
|--------|------|------|
| GET | `/health` | none |
| POST | `/auth/login` | researcher profile body |
| POST | `/query` | Bearer + `imaging:query` |
| GET | `/retrieve/{study_id}` | Bearer + `imaging:retrieve` |
| GET | `/audit` | none (demo) |
| GET | `/api/studies*` | Bearer (legacy endpoints locked down) |

## Portal API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/search` | Fan-out login+query; return aggregated results to user |
| POST | `/retrieve` | Re-auth to node; broker study payload to user |
| GET | `/profiles` | Demo researcher presets |
| GET | `/audit/{node}` | Proxy node audit log |

## Layout

```
auth/           # SSO policies, JWT, audit
portal/         # broker + UI + synonyms
shared/         # frozen contracts
search.py       # local study matcher
main.py         # hospital node app
tests/          # contract smoke harness
```
