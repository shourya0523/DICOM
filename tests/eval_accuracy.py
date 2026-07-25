"""Accuracy evaluation: concept detection + assertion for keyword vs OpenMed.

Gold labeling uses NegEx-lite over real Diagnosis text from hospital JSON:
a concept mention is NEGATED if it falls after a negation trigger in the same
sentence and before a clause terminator; otherwise PRESENT (uncertain/historical
cues are excluded from the gold set).

Usage:
  OPENMED_FORCE_FALLBACK=1 python -m tests.eval_accuracy
  OPENMED_SAMPLE=50 python -m tests.eval_accuracy --openmed
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_SRC = REPO_ROOT / "services" / "provider-gateway"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(GATEWAY_SRC))

from provider_gateway.config import CONCEPT_VOCAB
from provider_gateway.models import Assertion
from provider_gateway.openmed_adapter import OpenMedAdapter
from tests.hospital_data import load_hospital_studies

# NegEx-lite triggers / terminators (independent of production keyword cues)
NEG_TRIGGERS = re.compile(
    r"\b(?:"
    r"no evidence of|no signs? of|no definite|negative for|ruled out|"
    r"absence of|absent|denies|without|not seen|are not seen|is not seen|"
    r"no associated|no "
    r")\b",
    re.IGNORECASE,
)
UNCERTAIN = re.compile(
    r"\b(?:possible|possibly|probable|probably|suggestive of|cannot exclude|"
    r"may represent|suspicious for|likely|questionable)\b",
    re.IGNORECASE,
)
HISTORICAL = re.compile(
    r"\b(?:history of|prior|previous|status post|remote|s/p)\b",
    re.IGNORECASE,
)
FAMILY = re.compile(
    r"\b(?:family history|mother with|father with|sibling with)\b",
    re.IGNORECASE,
)
TERMINATORS = re.compile(r"[.;:]|\bbut\b|\bhowever\b|\balthough\b", re.IGNORECASE)


@dataclass
class GoldMention:
    study_id: str
    hospital: str
    code: str
    assertion: str  # PRESENT | NEGATED
    start: int
    end: int
    span: str


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    # assertion: among true-positive concept detections
    assertion_correct: int = 0
    assertion_total: int = 0
    assertion_by_gold: dict[str, dict[str, int]] = field(default_factory=dict)

    def add_assertion(self, gold: str, pred: str) -> None:
        self.assertion_total += 1
        if gold == pred:
            self.assertion_correct += 1
        bucket = self.assertion_by_gold.setdefault(gold, {})
        bucket[pred] = bucket.get(pred, 0) + 1

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def assertion_accuracy(self) -> float:
        return (
            self.assertion_correct / self.assertion_total
            if self.assertion_total
            else 0.0
        )


def sentence_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    start = 0
    for match in re.finditer(r"[.!?\n]+", text):
        end = match.start()
        if end > start:
            spans.append((start, end, text[start:end]))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text), text[start:]))
    return spans


def gold_assertion_in_sentence(sentence: str, match_local_start: int) -> str | None:
    """Return PRESENT/NEGATED, or None if uncertain/historical/family (exclude)."""
    before = sentence[:match_local_start]
    window = sentence[
        max(0, match_local_start - 80) : match_local_start + 40
    ]
    if FAMILY.search(window) or HISTORICAL.search(before) or UNCERTAIN.search(before):
        return None

    # Walk triggers in the sentence; scope runs until terminator.
    for trigger in NEG_TRIGGERS.finditer(sentence):
        if trigger.start() >= match_local_start:
            continue
        scope_start = trigger.end()
        term = TERMINATORS.search(sentence, scope_start)
        scope_end = term.start() if term else len(sentence)
        if scope_start <= match_local_start < scope_end:
            return "NEGATED"
    return "PRESENT"


def build_gold(hospitals: tuple[str, ...] = ("BCH", "MGH", "BWH")) -> list[GoldMention]:
    gold: list[GoldMention] = []
    for hospital in hospitals:
        for study in load_hospital_studies(hospital):
            text = study.Diagnosis
            lower = text.lower()
            for code, meta in CONCEPT_VOCAB.items():
                for pattern in meta["patterns"]:
                    pat = pattern.lower()
                    start = 0
                    while True:
                        idx = lower.find(pat, start)
                        if idx < 0:
                            break
                        # Resolve sentence containing the match
                        sent_start = idx
                        while sent_start > 0 and text[sent_start - 1] not in ".!?\n":
                            sent_start -= 1
                        sent_end = idx
                        while sent_end < len(text) and text[sent_end] not in ".!?\n":
                            sent_end += 1
                        sentence = text[sent_start:sent_end]
                        local = idx - sent_start
                        assertion = gold_assertion_in_sentence(sentence, local)
                        if assertion:
                            gold.append(
                                GoldMention(
                                    study_id=study.StudyID,
                                    hospital=hospital,
                                    code=code,
                                    assertion=assertion,
                                    start=idx,
                                    end=idx + len(pat),
                                    span=text[idx : idx + len(pat)],
                                )
                            )
                        start = idx + len(pat)
                        break  # one mention per pattern per study is enough
    # Deduplicate (study, code) keeping first
    seen: set[tuple[str, str, str]] = set()
    unique: list[GoldMention] = []
    for g in gold:
        key = (g.hospital, g.study_id, g.code)
        if key in seen:
            continue
        seen.add(key)
        unique.append(g)
    return unique


def evaluate_backend(
    name: str,
    adapter: OpenMedAdapter,
    gold: list[GoldMention],
    study_lookup: dict[tuple[str, str], str],
) -> dict:
    conf = Confusion()
    # Group gold by study
    by_study: dict[tuple[str, str], list[GoldMention]] = {}
    for g in gold:
        by_study.setdefault((g.hospital, g.study_id), []).append(g)

    t0 = time.perf_counter()
    for (hospital, study_id), mentions in by_study.items():
        text = study_lookup[(hospital, study_id)]
        result = adapter.process_diagnosis(text)
        pred = {c.code: c.assertion.value for c in result.concepts}
        gold_codes = {m.code: m.assertion for m in mentions}

        for code, gold_assertion in gold_codes.items():
            if code in pred:
                conf.tp += 1
                conf.add_assertion(gold_assertion, pred[code])
            else:
                conf.fn += 1

        for code in pred:
            if code not in gold_codes:
                # FP only if the pattern is NOT in the document at all —
                # otherwise gold excluded it (uncertain/historical).
                patterns = [p.lower() for p in CONCEPT_VOCAB[code]["patterns"]]
                lower = text.lower()
                if not any(p in lower for p in patterns):
                    conf.fp += 1
                # If pattern is in text but gold excluded (uncertain), don't count FP
                elif code not in gold_codes:
                    # Check if any gold-excluded reason — treat as neutral skip
                    pass

    elapsed = time.perf_counter() - t0

    # Per-code breakdown
    per_code: dict[str, Confusion] = {c: Confusion() for c in CONCEPT_VOCAB}
    for (hospital, study_id), mentions in by_study.items():
        text = study_lookup[(hospital, study_id)]
        # Reuse would be expensive for OpenMed — for per-code we recompute from
        # stored results only for keyword; for OpenMed we skip second pass by
        # caching predictions during the main loop.
    # Cache predictions during main loop instead
    return {
        "backend": name,
        "studies_evaluated": len(by_study),
        "gold_mentions": len(gold),
        "seconds": round(elapsed, 2),
        "concept_detection": {
            "tp": conf.tp,
            "fp": conf.fp,
            "fn": conf.fn,
            "precision": round(conf.precision, 4),
            "recall": round(conf.recall, 4),
            "f1": round(conf.f1, 4),
        },
        "assertion": {
            "correct": conf.assertion_correct,
            "total": conf.assertion_total,
            "accuracy": round(conf.assertion_accuracy, 4),
            "confusion_gold_to_pred": conf.assertion_by_gold,
        },
    }


def evaluate_backend_cached(
    name: str,
    adapter: OpenMedAdapter,
    gold: list[GoldMention],
    study_lookup: dict[tuple[str, str], str],
) -> dict:
    conf = Confusion()
    per_code: dict[str, Confusion] = {c: Confusion() for c in CONCEPT_VOCAB}
    by_study: dict[tuple[str, str], list[GoldMention]] = {}
    for g in gold:
        by_study.setdefault((g.hospital, g.study_id), []).append(g)

    preds: dict[tuple[str, str], dict[str, str]] = {}
    statuses: dict[str, int] = {}
    t0 = time.perf_counter()
    for key, mentions in by_study.items():
        text = study_lookup[key]
        result = adapter.process_diagnosis(text)
        statuses[result.extraction_status] = statuses.get(result.extraction_status, 0) + 1
        preds[key] = {c.code: c.assertion.value for c in result.concepts}
    elapsed = time.perf_counter() - t0

    for key, mentions in by_study.items():
        text = study_lookup[key]
        pred = preds[key]
        gold_codes = {m.code: m.assertion for m in mentions}

        for code, gold_assertion in gold_codes.items():
            pc = per_code[code]
            if code in pred:
                conf.tp += 1
                pc.tp += 1
                conf.add_assertion(gold_assertion, pred[code])
                pc.add_assertion(gold_assertion, pred[code])
            else:
                conf.fn += 1
                pc.fn += 1

        for code in pred:
            if code in gold_codes:
                continue
            patterns = [p.lower() for p in CONCEPT_VOCAB[code]["patterns"]]
            lower = text.lower()
            if not any(p in lower for p in patterns):
                conf.fp += 1
                per_code[code].fp += 1

    per_code_report = {}
    for code, c in sorted(per_code.items()):
        if c.tp + c.fp + c.fn == 0:
            continue
        per_code_report[code] = {
            "tp": c.tp,
            "fp": c.fp,
            "fn": c.fn,
            "precision": round(c.precision, 4),
            "recall": round(c.recall, 4),
            "f1": round(c.f1, 4),
            "assertion_accuracy": round(c.assertion_accuracy, 4),
            "assertion_n": c.assertion_total,
        }

    return {
        "backend": name,
        "studies_evaluated": len(by_study),
        "gold_mentions": len(gold),
        "seconds": round(elapsed, 2),
        "extraction_statuses": statuses,
        "concept_detection": {
            "tp": conf.tp,
            "fp": conf.fp,
            "fn": conf.fn,
            "precision": round(conf.precision, 4),
            "recall": round(conf.recall, 4),
            "f1": round(conf.f1, 4),
        },
        "assertion": {
            "correct": conf.assertion_correct,
            "total": conf.assertion_total,
            "accuracy": round(conf.assertion_accuracy, 4),
            "confusion_gold_to_pred": conf.assertion_by_gold,
        },
        "per_code": per_code_report,
    }


def main() -> None:
    run_openmed = "--openmed" in sys.argv
    sample_n = int(os.environ.get("OPENMED_SAMPLE", "80"))

    print("Building NegEx-lite gold from hospital JSON...", flush=True)
    gold_all = build_gold()
    present = sum(1 for g in gold_all if g.assertion == "PRESENT")
    negated = sum(1 for g in gold_all if g.assertion == "NEGATED")
    print(f"Gold mentions: {len(gold_all)} (PRESENT={present}, NEGATED={negated})", flush=True)

    study_lookup: dict[tuple[str, str], str] = {}
    for hospital in ("BCH", "MGH", "BWH"):
        for study in load_hospital_studies(hospital):
            study_lookup[(hospital, study.StudyID)] = study.Diagnosis

    # --- Keyword fallback on full gold ---
    os.environ["OPENMED_FORCE_FALLBACK"] = "1"
    kw_adapter = OpenMedAdapter()
    kw_adapter._available = False
    print("Evaluating keyword_fallback on full gold...", flush=True)
    kw_report = evaluate_backend_cached(
        "keyword_fallback", kw_adapter, gold_all, study_lookup
    )

    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gold": {
            "method": "NegEx-lite over Diagnosis text using CONCEPT_VOCAB patterns",
            "hospitals": ["BCH", "MGH", "BWH"],
            "mentions": len(gold_all),
            "present": present,
            "negated": negated,
            "concepts": len(CONCEPT_VOCAB),
        },
        "keyword_fallback": kw_report,
    }

    if run_openmed:
        # Sample gold studies for OpenMed (slow)
        os.environ["OPENMED_FORCE_FALLBACK"] = "0"
        live = OpenMedAdapter()
        live._available = None
        # Prefer balanced PRESENT/NEGATED sample
        present_g = [g for g in gold_all if g.assertion == "PRESENT"]
        negated_g = [g for g in gold_all if g.assertion == "NEGATED"]
        # Unique studies, interleaved
        selected_keys: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for pool in (present_g, negated_g):
            for g in pool:
                key = (g.hospital, g.study_id)
                if key in seen:
                    continue
                seen.add(key)
                selected_keys.append(key)
                if len(selected_keys) >= sample_n:
                    break
            if len(selected_keys) >= sample_n:
                break
        sample_gold = [
            g for g in gold_all if (g.hospital, g.study_id) in set(selected_keys)
        ]
        print(
            f"Evaluating OpenMed on {len(selected_keys)} studies "
            f"({len(sample_gold)} gold mentions)...",
            flush=True,
        )
        om_report = evaluate_backend_cached(
            "openmed", live, sample_gold, study_lookup
        )
        # Also score keyword on the same sample for fair head-to-head
        os.environ["OPENMED_FORCE_FALLBACK"] = "1"
        kw2 = OpenMedAdapter()
        kw2._available = False
        kw_sample = evaluate_backend_cached(
            "keyword_fallback_same_sample", kw2, sample_gold, study_lookup
        )
        report["openmed"] = om_report
        report["keyword_fallback_same_sample"] = kw_sample
        report["openmed_sample_studies"] = len(selected_keys)

    out = REPO_ROOT / "data" / "reports" / "accuracy_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nWrote {out}", flush=True)


if __name__ == "__main__":
    main()
