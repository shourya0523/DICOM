"""Performance benchmark for the Provider Gateway against the full dataset.

Usage:
    OPENMED_FORCE_FALLBACK=1 python -m tests.benchmark_gateway          # fallback path
    OPENMED_SAMPLE=25 python -m tests.benchmark_gateway --openmed      # + live OpenMed sample

Outputs JSON to stdout (and data/benchmark_report.json).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_SRC = REPO_ROOT / "services" / "provider-gateway"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(GATEWAY_SRC))

from provider_gateway.config import CONCEPT_VOCAB, Settings
from provider_gateway.models import (
    Assertion,
    CanonicalSearchQuery,
    ConceptFilter,
    SearchFilters,
)
from provider_gateway.openmed_adapter import OpenMedAdapter
from provider_gateway.pipeline import IngestionPipeline
from provider_gateway.repository import build_repository
from provider_gateway.search import SearchService
from provider_gateway.cohorts import CohortService
from provider_gateway.datasets import DatasetService

from tests.hospital_data import load_hospital_studies


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = min(len(values) - 1, max(0, int(round(p / 100 * (len(values) - 1)))))
    return values[k]


class StaticClient:
    def __init__(self, studies):
        self._studies = list(studies)

    def fetch_studies(self):
        return self._studies


def bench_hospital(code: str) -> dict:
    studies = load_hospital_studies(code)
    tmp = tempfile.mkdtemp()
    settings = Settings(
        provider_code=code,
        provider_name=code,
        node_url="http://localhost:0",
        database_path=str(Path(tmp) / f"{code.lower()}_bench.db"),
        token_secret="bench-secret",
        service_api_key="bench-key",
    )
    repo = build_repository(settings)
    adapter = OpenMedAdapter()
    pipeline = IngestionPipeline(settings, repo, StaticClient(studies), adapter)

    t0 = time.perf_counter()
    refresh = pipeline.refresh(warm_models=False)
    ingest_s = time.perf_counter() - t0

    # Per-study extraction latency (fallback path)
    per_doc: list[float] = []
    for study in studies[:200]:
        t = time.perf_counter()
        adapter.process_diagnosis(study.Diagnosis)
        per_doc.append((time.perf_counter() - t) * 1000)

    # Search latency across all concepts + a combined query
    search_service = SearchService(repo)
    cohort_service = CohortService(settings, repo)
    dataset_service = DatasetService(repo)

    queries = [
        CanonicalSearchQuery(
            query_id=f"bench-{c}",
            filters=SearchFilters(
                concepts=[ConceptFilter(code=c, assertion=Assertion.PRESENT)]
            ),
        )
        for c in sorted(CONCEPT_VOCAB)
    ]
    queries.append(
        CanonicalSearchQuery(
            query_id="bench-combined",
            filters=SearchFilters(
                age_max=21,
                modalities=["MR"],
                body_parts=["BRAIN"],
                concepts=[
                    ConceptFilter(code="HYDROCEPHALUS", assertion=Assertion.PRESENT)
                ],
            ),
        )
    )

    search_ms: list[float] = []
    concept_counts: dict[str, int] = {}
    last_cohort = None
    for query in queries:
        for _ in range(3):
            t = time.perf_counter()
            matches, _fp = search_service.execute(query)
            search_ms.append((time.perf_counter() - t) * 1000)
        if query.filters.concepts:
            concept_counts[query.filters.concepts[0].code] = len(matches)
        cohort = cohort_service.freeze(query, matches, index_version="openmed-v1")
        last_cohort = cohort

    # Dataset preview latency
    t = time.perf_counter()
    preview = dataset_service.generate_preview(provider=code, cohort=last_cohort)
    preview_ms = (time.perf_counter() - t) * 1000

    db_bytes = Path(settings.database_path).stat().st_size

    # Assertion mix across whole index
    records = repo.list_evidence()
    assertion_mix: dict[str, int] = {}
    concepts_per_record: list[int] = []
    for record in records:
        concepts_per_record.append(len(record.concepts))
        for concept in record.concepts:
            assertion_mix[concept.assertion.value] = (
                assertion_mix.get(concept.assertion.value, 0) + 1
            )

    return {
        "hospital": code,
        "studies": len(studies),
        "refresh": {
            "status": refresh.status,
            "ingested": refresh.ingested,
            "failed": refresh.failed,
            "seconds": round(ingest_s, 3),
            "studies_per_second": round(len(studies) / ingest_s, 1),
        },
        "extraction_ms": {
            "mean": round(statistics.mean(per_doc), 3),
            "p50": round(pct(per_doc, 50), 3),
            "p95": round(pct(per_doc, 95), 3),
            "max": round(max(per_doc), 3),
            "sampled": len(per_doc),
        },
        "search_ms": {
            "queries": len(queries),
            "runs": len(search_ms),
            "mean": round(statistics.mean(search_ms), 2),
            "p50": round(pct(search_ms, 50), 2),
            "p95": round(pct(search_ms, 95), 2),
            "max": round(max(search_ms), 2),
        },
        "dataset_preview_ms": round(preview_ms, 2),
        "db_size_kb": round(db_bytes / 1024, 1),
        "concept_present_counts": concept_counts,
        "assertion_mix": assertion_mix,
        "concepts_per_record_mean": round(statistics.mean(concepts_per_record), 2),
    }


def bench_openmed_sample(n: int) -> dict:
    os.environ["OPENMED_FORCE_FALLBACK"] = "0"
    adapter = OpenMedAdapter()
    if not adapter.available():
        return {"available": False}
    studies = load_hospital_studies("BCH")[:n]

    # Warm (first call includes model load)
    t0 = time.perf_counter()
    adapter.process_diagnosis(studies[0].Diagnosis)
    warm_s = time.perf_counter() - t0

    per_doc: list[float] = []
    statuses: dict[str, int] = {}
    pii_total = 0
    for study in studies[1:]:
        t = time.perf_counter()
        result = adapter.process_diagnosis(study.Diagnosis)
        per_doc.append((time.perf_counter() - t) * 1000)
        statuses[result.extraction_status] = statuses.get(result.extraction_status, 0) + 1
        pii_total += result.pii_entity_count

    mean_ms = statistics.mean(per_doc)
    return {
        "available": True,
        "sampled_docs": len(per_doc),
        "first_call_model_load_s": round(warm_s, 1),
        "per_doc_ms": {
            "mean": round(mean_ms, 1),
            "p50": round(pct(per_doc, 50), 1),
            "p95": round(pct(per_doc, 95), 1),
            "max": round(max(per_doc), 1),
        },
        "statuses": statuses,
        "pii_entities_total": pii_total,
        "projected_900_studies_min": round(mean_ms * 900 / 60000, 1),
        "projected_2700_studies_min": round(mean_ms * 2700 / 60000, 1),
    }


def main() -> None:
    run_openmed = "--openmed" in sys.argv
    os.environ.setdefault("OPENMED_FORCE_FALLBACK", "1")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "keyword_fallback",
        "hospitals": [],
    }
    total_t0 = time.perf_counter()
    for code in ("BCH", "MGH", "BWH"):
        report["hospitals"].append(bench_hospital(code))
    report["total_seconds_all_hospitals"] = round(time.perf_counter() - total_t0, 2)
    report["total_studies"] = sum(h["studies"] for h in report["hospitals"])

    if run_openmed:
        sample_n = int(os.environ.get("OPENMED_SAMPLE", "25"))
        report["openmed_live_sample"] = bench_openmed_sample(sample_n)

    out = REPO_ROOT / "data" / "reports" / "benchmark_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
