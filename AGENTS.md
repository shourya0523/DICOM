# AGENTS.md

## Cursor Cloud specific instructions

### What this is
FastAPI app simulating a single "hospital node" for a federated medical imaging hackathon. See `README.md` for full API docs. One codebase, run as up to three instances on different ports. Stateless: no DB, loads a JSON file into memory at startup.

### Services
The same app (`main:app`) is run three times, one per hospital. The `HOSPITAL_NODE` env var (`BCH`/`MGH`/`BWH`) picks which `data/*.json` file loads. Invalid/unset defaults to `BCH` (see `main.py`).

| Node | Env | Port | Data file |
| ---- | --- | ---- | --------- |
| BCH | `HOSPITAL_NODE=BCH` | 8001 | `data/bch_data.json` |
| MGH | `HOSPITAL_NODE=MGH` | 8002 | `data/mgh_data.json` |
| BWH | `HOSPITAL_NODE=BWH` | 8003 | `data/bwh_data.json` |

### Running (dev)
Dependencies install into a local `venv/` (created by the update script; git-ignored). Prefix commands with `./venv/bin/`.

```bash
HOSPITAL_NODE=BCH ./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001 --reload
HOSPITAL_NODE=MGH ./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8002 --reload
HOSPITAL_NODE=BWH ./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8003 --reload
```

Verify: `curl http://localhost:8001/health` -> `{"status":"healthy","node":"BCH"}`. Swagger UI at `/docs`. Endpoints: `GET /health`, `GET /api/studies`, `GET /api/studies/{study_id}`.

### Notes / gotchas
- No test suite and no linter config exist in this repo. "Build" is not applicable (pure Python, no build step); running the app is the verification.
- `scripts/` (the Gemini data generator) is git-ignored and not present in the repo; the pre-generated `data/*.json` files are all that's needed to run.
- `--reload` hot-reloads code, but data is read once at startup — editing a `data/*.json` file requires restarting the node.
