"""OpenMed adapter — local de-identification and clinical concept extraction."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from provider_gateway.config import CONCEPT_VOCAB
from provider_gateway.models import Assertion, CodedConcept, OpenMedAdapterResult
from provider_gateway.redaction import safety_mask

logger = logging.getLogger("provider_gateway.openmed_adapter")

DEID_MODEL = "OpenMed/OpenMed-PII-SuperClinical-Small-44M-v1"
# TinyMed-135M — proven working default; override with OPENMED_NER_MODEL if needed.
DEFAULT_NER_MODEL = "disease_detection_tiny"

NEG_TRIGGERS = re.compile(
    r"\b(?:"
    r"no evidence of|no signs? of|no definite|negative for|ruled out|"
    r"absence of|absent|denies|without|not seen|are not seen|is not seen|"
    r"no associated|no "
    r")\b",
    re.IGNORECASE,
)
TERMINATORS = re.compile(r"[.;:]|\bbut\b|\bhowever\b|\balthough\b", re.IGNORECASE)
UNCERTAIN_CUES = re.compile(
    r"\b(?:possible|possibly|probable|probably|suggestive of|cannot exclude|"
    r"may represent|suspicious for|likely|questionable)\b",
    re.IGNORECASE,
)
HISTORICAL_CUES = re.compile(
    r"\b(?:history of|prior|previous|status post|s/p|remote)\b",
    re.IGNORECASE,
)
FAMILY_CUES = re.compile(
    r"\b(?:family history|mother with|father with|sibling with)\b",
    re.IGNORECASE,
)


def resolve_ner_model() -> str:
    return os.environ.get("OPENMED_NER_MODEL", DEFAULT_NER_MODEL).strip() or DEFAULT_NER_MODEL


def negex_assertion(document: str, match_start: int, match_end: int | None = None) -> Assertion:
    """NegEx-lite: negation scope runs from trigger to clause terminator."""
    if match_start < 0:
        return Assertion.PRESENT
    end = match_end if match_end is not None else match_start + 1
    sent_start = match_start
    while sent_start > 0 and document[sent_start - 1] not in ".!?\n":
        sent_start -= 1
    sent_end = end
    while sent_end < len(document) and document[sent_end] not in ".!?\n":
        sent_end += 1
    sentence = document[sent_start:sent_end]
    local_start = match_start - sent_start
    before = sentence[:local_start]
    window = sentence[max(0, local_start - 80) : local_start + 40]

    if FAMILY_CUES.search(window):
        return Assertion.FAMILY_HISTORY
    if HISTORICAL_CUES.search(before):
        return Assertion.HISTORICAL
    if UNCERTAIN_CUES.search(before):
        return Assertion.UNCERTAIN

    for trigger in NEG_TRIGGERS.finditer(sentence):
        if trigger.start() >= local_start:
            continue
        scope_start = trigger.end()
        term = TERMINATORS.search(sentence, scope_start)
        scope_end = term.start() if term else len(sentence)
        if scope_start <= local_start < scope_end:
            return Assertion.NEGATED
    return Assertion.PRESENT


def merge_concepts(
    primary: list[CodedConcept],
    secondary: list[CodedConcept],
) -> list[CodedConcept]:
    """Union by code. Prefer openmed extractor; if assertions disagree, prefer NEGATED."""
    priority = {
        Assertion.NEGATED: 4,
        Assertion.FAMILY_HISTORY: 3,
        Assertion.HISTORICAL: 2,
        Assertion.UNCERTAIN: 1,
        Assertion.PRESENT: 0,
    }
    merged: dict[str, CodedConcept] = {c.code: c for c in secondary}
    for concept in primary:
        existing = merged.get(concept.code)
        if existing is None:
            merged[concept.code] = concept
            continue
        # Prefer openmed as extractor label when either side is openmed
        extractor = (
            "openmed"
            if "openmed" in {concept.extractor, existing.extractor}
            else concept.extractor
        )
        assertion = (
            concept.assertion
            if priority[concept.assertion] >= priority[existing.assertion]
            else existing.assertion
        )
        confidence = max(concept.confidence, existing.confidence)
        merged[concept.code] = CodedConcept(
            code=concept.code,
            display=concept.display,
            assertion=assertion,
            confidence=confidence,
            extractor=extractor,  # type: ignore[arg-type]
        )
    return list(merged.values())


class OpenMedAdapter:
    def __init__(self, ner_model: str | None = None) -> None:
        self._available: bool | None = None
        self._warmed = False
        self._loader = None
        self.ner_model = ner_model or resolve_ner_model()

    def available(self) -> bool:
        if os.environ.get("OPENMED_FORCE_FALLBACK", "").lower() in {"1", "true", "yes"}:
            return False
        if self._available is not None:
            return self._available
        try:
            import openmed  # noqa: F401

            self._available = True
        except Exception:
            self._available = False
        return self._available

    def warm(self) -> None:
        if self._warmed or not self.available():
            return
        try:
            from openmed import prefetch_model

            prefetch_model(self.ner_model)
            self._warmed = True
        except Exception:
            logger.warning("OPENMED_WARM_FAILED")

    def process_diagnosis(self, text: str) -> OpenMedAdapterResult:
        start = time.perf_counter()
        if not text or not text.strip():
            return OpenMedAdapterResult(
                extraction_status="failed",
                latency_ms=0,
                model_info={"backend": "none"},
            )

        safe_text = safety_mask(text)
        if not self.available():
            concepts = self._keyword_extract(safe_text)
            return OpenMedAdapterResult(
                concepts=concepts,
                extraction_status="fallback",
                latency_ms=int((time.perf_counter() - start) * 1000),
                model_info={"backend": "keyword_fallback", "ner_model": None},
            )

        try:
            result = self._run_openmed(safe_text)
            result.latency_ms = int((time.perf_counter() - start) * 1000)
            return result
        except Exception:
            logger.warning("OPENMED_EXTRACTION_FAILED")
            concepts = self._keyword_extract(safe_text)
            return OpenMedAdapterResult(
                concepts=concepts,
                extraction_status="fallback",
                latency_ms=int((time.perf_counter() - start) * 1000),
                model_info={"backend": "keyword_fallback", "ner_model": self.ner_model},
            )

    def _run_openmed(self, text: str) -> OpenMedAdapterResult:
        from openmed import analyze_text, deidentify

        deid_model = DEID_MODEL
        backend = "hf"
        pii_count = 0
        pii_types: list[str] = []
        working_text = text

        try:
            deid = deidentify(
                text,
                method="mask",
                model_name=deid_model,
                keep_mapping=False,
                cache_results=False,
                confidence_threshold=0.7,
                lang="en",
            )
            working_text = safety_mask(deid.deidentified_text or text)
            entities = getattr(deid, "pii_entities", None) or []
            pii_count = len(entities)
            pii_types = sorted(
                {
                    getattr(e, "label", None)
                    or getattr(e, "entity_type", None)
                    or "PII"
                    for e in entities
                }
            )
            del deid
        except Exception:
            working_text = safety_mask(text)
            logger.warning("OPENMED_DEID_FAILED")

        ner = analyze_text(
            working_text,
            model_name=self.ner_model,
            confidence_threshold=0.5,
            output_format="dict",
        )
        raw_entities = []
        if isinstance(ner, dict):
            raw_entities = ner.get("entities") or []
        else:
            raw_entities = getattr(ner, "entities", None) or []

        ner_concepts = self._map_entities(working_text, raw_entities)
        # Always union with vocab keyword hits so imaging jargon (shunt,
        # ventriculomegaly, nonspecific mass/lesion) is not dropped when NER
        # returns other disease spans.
        keyword_concepts = self._keyword_extract(working_text)
        concepts = merge_concepts(ner_concepts, keyword_concepts)

        if not concepts:
            status = "failed"
        else:
            # OpenMed path completed successfully; keyword union is intentional.
            status = "ok"

        return OpenMedAdapterResult(
            concepts=concepts,
            pii_entity_count=pii_count,
            pii_types=pii_types,
            model_info={
                "deid_model": deid_model,
                "ner_model": self.ner_model,
                "backend": backend,
                "ner_hits": len(ner_concepts),
                "keyword_hits": len(keyword_concepts),
                "merged": len(concepts),
            },
            extraction_status=status,  # type: ignore[arg-type]
        )

    def _map_entities(self, text: str, entities: list[Any]) -> list[CodedConcept]:
        mapped: dict[str, CodedConcept] = {}
        lower = text.lower()
        for entity in entities:
            if isinstance(entity, dict):
                ent_text = str(entity.get("text") or "")
                confidence = float(entity.get("confidence") or entity.get("score") or 0.5)
                start = entity.get("start")
                end = entity.get("end")
            else:
                ent_text = str(getattr(entity, "text", "") or "")
                confidence = float(getattr(entity, "confidence", 0.5) or 0.5)
                start = getattr(entity, "start", None)
                end = getattr(entity, "end", None)

            code = self._lookup_code(ent_text)
            if not code:
                continue

            # Prefer NegEx over ConText for vocab-mapped spans — ConText wrongly
            # negates leads like "Chiari … without ventriculomegaly".
            if start is not None:
                try:
                    assertion = negex_assertion(text, int(start), int(end) if end is not None else None)
                except Exception:
                    assertion = self._assert_with_context(text, ent_text, start, end)
            else:
                idx = lower.find(ent_text.lower())
                assertion = (
                    negex_assertion(text, idx, idx + len(ent_text))
                    if idx >= 0
                    else self._assert_with_context(text, ent_text, start, end)
                )

            display = CONCEPT_VOCAB[code]["display"]
            existing = mapped.get(code)
            if existing is None or confidence > existing.confidence:
                mapped[code] = CodedConcept(
                    code=code,
                    display=display,
                    assertion=assertion,
                    confidence=min(max(confidence, 0.0), 1.0),
                    extractor="openmed",
                )
        return list(mapped.values())

    def _keyword_extract(self, text: str) -> list[CodedConcept]:
        lower = text.lower()
        found: list[CodedConcept] = []
        for code, meta in CONCEPT_VOCAB.items():
            for pattern in meta["patterns"]:
                idx = lower.find(pattern.lower())
                if idx < 0:
                    continue
                assertion = negex_assertion(text, idx, idx + len(pattern))
                found.append(
                    CodedConcept(
                        code=code,
                        display=meta["display"],
                        assertion=assertion,
                        confidence=0.7,
                        extractor="keyword_fallback",
                    )
                )
                break
        return found

    # Back-compat for tests that call the old private helpers
    def _keyword_fallback(self, text: str) -> list[CodedConcept]:
        return self._keyword_extract(text)

    def _sentence_at(self, text: str, index: int) -> str:
        start = index
        while start > 0 and text[start - 1] not in ".!?\n":
            start -= 1
        end = index
        while end < len(text) and text[end] not in ".!?\n":
            end += 1
        return text[start:end]

    def _expected_prefix_assertion(self, text: str, match_index: int) -> Assertion:
        return negex_assertion(text, match_index)

    def _lookup_code(self, entity_text: str) -> str | None:
        lower = entity_text.lower()
        for code, meta in CONCEPT_VOCAB.items():
            for pattern in meta["patterns"]:
                if pattern.lower() in lower or lower in pattern.lower():
                    return code
        return None

    def _assert_with_context(
        self, document: str, span_text: str, start: Any, end: Any
    ) -> Assertion:
        try:
            from openmed.clinical.context import resolve_span_context

            span = {
                "text": span_text,
                "start": int(start) if start is not None else 0,
                "end": int(end) if end is not None else len(span_text),
                "document_text": document,
                "context": document,
            }
            ctx = resolve_span_context(span)
            negation = getattr(ctx, "negation", None) or getattr(ctx, "negated", None)
            temporality = getattr(ctx, "temporality", None)
            certainty = getattr(ctx, "certainty", None)
            if str(negation).lower() in {"negated", "true", "1"}:
                return Assertion.NEGATED
            if str(temporality).lower() in {"historical", "history"}:
                return Assertion.HISTORICAL
            if str(certainty).lower() in {"uncertain", "possible"}:
                return Assertion.UNCERTAIN
            return Assertion.PRESENT
        except Exception:
            if start is not None:
                try:
                    return negex_assertion(document, int(start), int(end) if end is not None else None)
                except Exception:
                    pass
            return Assertion.PRESENT
