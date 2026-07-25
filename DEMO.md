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
# Terminal A
./scripts/start_nodes.sh
# Terminal B
./scripts/start_portal.sh
# Terminal C
./tests/contract_smoke.sh
```

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
