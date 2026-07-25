# Demo Script & Pitch Notes

## Live demo walkthrough (rehearse twice)

1. Open http://127.0.0.1:8010
2. Select **Harvard + IRB** (`jorgenson@harvard.edu`). Search `pediatric brain MRI`.
   - **BCH**: SSO OK, `full_metadata`, studies listed
   - **MGH**: SSO OK, `count_only`
   - **BWH**: `denied` at SSO (Harvard email not on BWH allowlist)
3. Select **Guest**. Same search → all nodes `denied_at_sso`.
4. Select **Harvard + IRB**. Search `lissencephaly` → BCH returns `suppressed` (rare cohort).
5. From a BCH study ID (or `BR-1543`), click **Retrieve via portal** as Harvard → success.
6. Switch to **MIT (no IRB)** → retrieve denied (missing `imaging:retrieve`).
7. Load **BCH audit** → show allow / deny / suppress decisions.

## Startup

```bash
pip install -r requirements.txt
# Terminal A — BCH/MGH/BWH hospital nodes on :8001-8003
./scripts/start_nodes.sh
# Terminal B — portal UI on :8010
./scripts/start_portal.sh
# Terminal C
./tests/contract_smoke.sh
```

Or in containers: `docker compose --profile portal up --build` (see below).

## Known gaps (own these in the pitch)

- Consent/IRB status of underlying studies is not modeled per-record.
- Suppression uses a fixed threshold (k=5), not differential privacy / anti-probing.
- Identity root of trust is mocked per-node `.edu` allowlists (not IHE XUA / real federation).
- Field-level re-identification (age + rare diagnosis + site) is only partly mitigated via PII redaction.
- No “not enough data yet — expand cohort” feedback loop.

## Precedent to cite

- **SHRINE / ENACT** — federated hub-and-spoke aggregate counts
- **TriNetX** — commercial analog at scale
- **SMART on FHIR + OAuth2** — scoped token pattern (`imaging:query` / `imaging:retrieve`)
- **NIST SP 800-207** — zero trust; PEP at each hospital node

---

# Local Demo Guide

Everything needed to run the federated imaging demo from one laptop, from one folder.

## Layout

```
.
├── compose.yml                  # local demo stack (add more services here)
├── .env.example                 # copy to .env before starting
├── Dockerfile                   # multi-stage: gateway + hospital-node + portal
├── pyproject.toml               # installs provider_gateway from services/provider-gateway
├── DEMO.md / README.md
├── services/
│   ├── hospital-node/           # hospital edge app: main.py, models.py, search.py, auth/
│   ├── portal/portal/           # researcher-facing portal (platform SSO + fan-out)
│   └── provider-gateway/
│       └── provider_gateway/    # installable gateway package
├── shared/                      # frozen cross-service contracts + vocab
├── data/
│   ├── hospitals/               # bch/mgh/bwh_data.json (committed)
│   ├── gateway/                 # SQLite state (generated, gitignored)
│   └── reports/                 # accuracy/benchmark output (generated, gitignored)
├── scripts/                     # start_nodes.sh, start_portal.sh
├── tests/                       # pytest suite + eval/benchmark/contract-smoke scripts
└── tools/
    ├── run_local_demo.sh        # no-Docker node+gateway launcher
    └── dev_explorer.py          # runs all three nodes on :9001-9003
```

Every service imports `shared` from the repo root, so run commands from the repo root
(the launcher scripts and the images set `PYTHONPATH` for you).

## Ports

The coordinator only ever calls the **gateway** port.

| Service | Port | Notes |
| --- | --- | --- |
| BCH gateway / node | `8101` / `8001` | coordinator entry point / raw node |
| MGH gateway / node | `8102` / `8002` | |
| BWH gateway / node | `8103` / `8003` | |
| Portal UI | `8010` | researcher-facing demo UI |

## Option A — Docker Compose (recommended)

```bash
cp .env.example .env
# edit .env: set TOKEN_SECRET and SERVICE_API_KEY
docker compose up --build                    # BCH node :8001 + provider gateway :8101
docker compose --profile portal up --build   # + MGH/BWH nodes and the portal on :8010
```

In a second terminal:

```bash
docker compose ps
curl http://localhost:8001/health                              # hospital node
curl http://localhost:8101/health                              # provider gateway
curl -X POST http://localhost:8101/refresh -H "X-API-Key: $SERVICE_API_KEY"
curl http://localhost:8101/capabilities -H "X-API-Key: $SERVICE_API_KEY"
```

Stop with `docker compose down` (add `-v` to also drop the gateway SQLite volume).

Run a second provider alongside the first:

```bash
PROVIDER_CODE=MGH PROVIDER_NAME="Massachusetts General Hospital" \
GATEWAY_PORT=8102 NODE_PORT=8002 \
docker compose -p mgh up --build -d
```

## Option B — No Docker

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -e .

cp .env.example .env    # optional; the script falls back to demo defaults
./tools/run_local_demo.sh
```

This starts the BCH node on `:8001` and the BCH gateway on `:8101` in one terminal
and prints the follow-up `curl` commands. `PROVIDER_CODE=MGH ./tools/run_local_demo.sh`
starts that provider on its own ports instead.

To run all three gateways against already-running nodes:

```bash
python -m provider_gateway.run_gateways
```

## Demo script (what to show)

1. `curl http://localhost:8001/api/studies | head` — the raw node leaks PII on purpose.
2. `POST /refresh` on the gateway — ingestion de-identifies and indexes locally.
3. `POST /search` on the gateway — banded counts and redacted evidence only.

```bash
curl http://localhost:8101/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SERVICE_API_KEY" \
  -d '{
    "query_id": "query-1",
    "filters": { "modalities": ["MR", "CT"], "body_parts": ["BRAIN"] },
    "freeze_cohort": true
  }'
```

`modalities` and `body_parts` are **lists** in every request and response — that is the
frozen coordinator-facing contract.

4. Gateway console UI: <http://localhost:8101/>

When the hospital node enforces SSO, the gateway logs in as its configured service account
(`NODE_SERVICE_ID` / `NODE_SERVICE_ORG` / `NODE_SERVICE_IRB_APPROVED`) before ingesting.
That identity must be on the node's allowlist and IRB-approved, otherwise `/refresh` returns
records without PII and skips them.

## Adding a service later

1. Create `services/<name>/` (add a stage to the root `Dockerfile`, or give it its own).
2. Uncomment/extend the `coordinator` placeholder block in `compose.yml`.
3. Point it at `http://provider-gateway:8101` inside the compose network, or
   `http://localhost:8101` from the host.
4. Put anything two services must agree on in `shared/` — it is already on `PYTHONPATH`
   and copied into every image.

## Tests

```bash
OPENMED_FORCE_FALLBACK=1 python -m pytest
python tests/contract_smoke.py                              # needs nodes + portal running
OPENMED_FORCE_FALLBACK=1 python tests/eval_accuracy.py      # -> data/reports/
OPENMED_FORCE_FALLBACK=1 python tests/benchmark_gateway.py  # -> data/reports/
```

## Troubleshooting

- **`TOKEN_SECRET` / `SERVICE_API_KEY` error on `docker compose up`** — you skipped
  `cp .env.example .env`, or left the placeholders empty.
- **Port already in use** — override `GATEWAY_PORT` / `NODE_PORT` in `.env`.
- **Docker flaky** — use Option B; it needs nothing but Python 3.10+.
- **Search returns nothing** — you haven't called `POST /refresh` since startup.
- **Slow first start** — set `OPENMED_FORCE_FALLBACK=1` to skip the model download.
