# Coordinator Service

Central portal (Flask API) for the federated DICOM search network. The frontend
service calls this API; the coordinator fans queries out to the hospital nodes,
aggregates results, and enforces auth / privacy policy.

```
Frontend ──> Coordinator (this, Flask :5001) ──> BCH :8001
                                             ├──> MGH :8002
                                             └──> BWH :8003
```

## Run

```bash
cd coordinator
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional; sensible defaults are baked in
python run.py               # serves on http://localhost:5001
```

## Endpoints (so far)

| Method | Path         | Description                                          |
| ------ | ------------ | ---------------------------------------------------- |
| `GET`  | `/health`    | Liveness check for the coordinator                   |
| `GET`  | `/api/nodes` | Dev-only direct reachability of node ports           |
| `POST` | `/search`    | Main entry: deduce filters, resolve, fan out to gateway |

### `POST /search`

Request (`nl-string` mandatory, `filters` optional):

```json
{
  "query_id": "q-123",
  "nl-string": "fetal MRI at 20 weeks with ventriculomegaly",
  "filters": {
    "patient_age_min": null, "patient_age_max": null,
    "gestational_age_min_weeks": 20, "gestational_age_max_weeks": 20,
    "modality": ["MR"], "body_part": ["FETAL"],
    "concepts": [{"code": "VENTRICULOMEGALY", "assertion": "PRESENT"}]
  }
}
```

Flow: user filters are authoritative → Gemini deduces only the gaps from the NL
string (constrained to `app/vocab.py`) → every value re-validated against the
vocab → resolved filters returned to the frontend → query fanned out to the
gateway. Response includes `resolved_filters`, per-field `filter_provenance`
(`user` | `gemini` | `none`), `gemini` status, and `results`.

> Without `GEMINI_API_KEY` set, deduction is skipped (status `skipped_no_api_key`)
> and the service runs on user filters alone. Gateway calls are **stubbed**
> until the three real gateway URLs are provided.

## Layout

```
coordinator/
├── run.py                        # dev entrypoint
├── config.py                     # env-driven config (Gemini, gateway, nodes)
├── requirements.txt
├── .env.example
└── app/
    ├── __init__.py               # create_app() factory (CORS, blueprints)
    ├── routes.py                 # HTTP routes (/search, /health, /api/nodes)
    ├── vocab.py                  # controlled vocabulary + normalizers (source of truth)
    └── services/
        ├── gemini_client.py      # NL → structured filters (enforced JSON schema)
        ├── filter_resolver.py    # merge: user wins, Gemini fills gaps, validate
        ├── gateway_client.py     # fan-out to gateway's 3 endpoints (STUBBED)
        └── node_client.py        # dev-only direct node health check
```
