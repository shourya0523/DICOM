"""SQLite persistence for privacy-safe Gateway state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from provider_gateway.config import Settings, utcnow
from provider_gateway.models import (
    AccessRequest,
    AccessRequestEvent,
    AccessRequestStatus,
    ClinicalEvidenceRecord,
    CodedConcept,
    Cohort,
    DatasetPreview,
    OrganisationPolicy,
    OrgStatus,
)


def _dt(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


class Repository:
    def __init__(self, database_path: str):
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clinical_evidence (
                    study_token TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    age_years REAL NOT NULL,
                    age_bucket TEXT NOT NULL,
                    sex TEXT NOT NULL,
                    study_year INTEGER NOT NULL,
                    modalities_json TEXT NOT NULL,
                    body_parts_json TEXT NOT NULL,
                    gestational_age_weeks INTEGER,
                    concepts_json TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    extraction_status TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS token_map (
                    study_token TEXT PRIMARY KEY,
                    source_study_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cohorts (
                    cohort_handle TEXT PRIMARY KEY,
                    query_id TEXT NOT NULL,
                    query_fingerprint TEXT NOT NULL,
                    member_tokens_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    index_version TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cohorts_fingerprint
                    ON cohorts(query_fingerprint);

                CREATE TABLE IF NOT EXISTS organisations (
                    organisation_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_auto_approval INTEGER NOT NULL,
                    data_auto_approval INTEGER NOT NULL,
                    allowed_metadata_fields_json TEXT NOT NULL,
                    allowed_data_fields_json TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    policy_version TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS access_requests (
                    provider_request_id TEXT PRIMARY KEY,
                    coordinator_access_request_id TEXT NOT NULL UNIQUE,
                    organisation_id TEXT NOT NULL,
                    researcher_id TEXT NOT NULL,
                    cohort_handle TEXT NOT NULL,
                    project_title TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    requested_metadata_fields_json TEXT NOT NULL,
                    requested_data_fields_json TEXT NOT NULL,
                    approved_fields_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approval_path TEXT,
                    decision_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    dataset_id TEXT,
                    dataset_preview_json TEXT
                );

                CREATE TABLE IF NOT EXISTS access_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_request_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    FOREIGN KEY(provider_request_id) REFERENCES access_requests(provider_request_id)
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gateway_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._migrate_clinical_evidence_schema(conn)

    def _migrate_clinical_evidence_schema(self, conn: sqlite3.Connection) -> None:
        """Rebuild clinical_evidence when singular modality/body_part columns remain."""
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(clinical_evidence)").fetchall()
        }
        if not columns:
            return
        needs_rebuild = "modality" in columns or "modalities_json" not in columns
        if not needs_rebuild and "gestational_age_weeks" not in columns:
            needs_rebuild = True
        if not needs_rebuild:
            return
        # MVP local DBs are regenerated via /refresh; drop obsolete singular schema.
        conn.execute("DROP TABLE IF EXISTS clinical_evidence")
        conn.execute(
            """
            CREATE TABLE clinical_evidence (
                study_token TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                age_years REAL NOT NULL,
                age_bucket TEXT NOT NULL,
                sex TEXT NOT NULL,
                study_year INTEGER NOT NULL,
                modalities_json TEXT NOT NULL,
                body_parts_json TEXT NOT NULL,
                gestational_age_weeks INTEGER,
                concepts_json TEXT NOT NULL,
                pipeline_version TEXT NOT NULL,
                extraction_status TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM token_map")
        conn.execute("DELETE FROM cohorts")
        conn.execute("DELETE FROM gateway_meta WHERE key = 'index_timestamp'")


    def bootstrap_organisations(self, orgs: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            for raw in orgs:
                policy = OrganisationPolicy(
                    organisation_id=raw["organisation_id"],
                    display_name=raw["display_name"],
                    status=OrgStatus(raw["status"]),
                    metadata_auto_approval=bool(raw["metadata_auto_approval"]),
                    data_auto_approval=bool(raw["data_auto_approval"]),
                    allowed_metadata_fields=list(raw.get("allowed_metadata_fields") or []),
                    allowed_data_fields=list(raw.get("allowed_data_fields") or []),
                    valid_from=_dt(raw.get("valid_from")),
                    valid_to=_dt(raw.get("valid_to")),
                    policy_version=raw.get("policy_version", "v1"),
                )
                conn.execute(
                    """
                    INSERT INTO organisations (
                        organisation_id, display_name, status,
                        metadata_auto_approval, data_auto_approval,
                        allowed_metadata_fields_json, allowed_data_fields_json,
                        valid_from, valid_to, policy_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(organisation_id) DO UPDATE SET
                        display_name=excluded.display_name,
                        status=excluded.status,
                        metadata_auto_approval=excluded.metadata_auto_approval,
                        data_auto_approval=excluded.data_auto_approval,
                        allowed_metadata_fields_json=excluded.allowed_metadata_fields_json,
                        allowed_data_fields_json=excluded.allowed_data_fields_json,
                        valid_from=excluded.valid_from,
                        valid_to=excluded.valid_to,
                        policy_version=excluded.policy_version
                    """,
                    (
                        policy.organisation_id,
                        policy.display_name,
                        policy.status.value,
                        int(policy.metadata_auto_approval),
                        int(policy.data_auto_approval),
                        _dumps(policy.allowed_metadata_fields),
                        _dumps(policy.allowed_data_fields),
                        policy.valid_from.isoformat() if policy.valid_from else None,
                        policy.valid_to.isoformat() if policy.valid_to else None,
                        policy.policy_version,
                    ),
                )

    def upsert_evidence(self, record: ClinicalEvidenceRecord, source_study_id: str) -> None:
        now = utcnow().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO clinical_evidence (
                    study_token, provider, age_years, age_bucket, sex, study_year,
                    modalities_json, body_parts_json, gestational_age_weeks,
                    concepts_json, pipeline_version, extraction_status, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(study_token) DO UPDATE SET
                    provider=excluded.provider,
                    age_years=excluded.age_years,
                    age_bucket=excluded.age_bucket,
                    sex=excluded.sex,
                    study_year=excluded.study_year,
                    modalities_json=excluded.modalities_json,
                    body_parts_json=excluded.body_parts_json,
                    gestational_age_weeks=excluded.gestational_age_weeks,
                    concepts_json=excluded.concepts_json,
                    pipeline_version=excluded.pipeline_version,
                    extraction_status=excluded.extraction_status,
                    ingested_at=excluded.ingested_at
                """,
                (
                    record.study_token,
                    record.provider,
                    record.age_years,
                    record.age_bucket,
                    record.sex,
                    record.study_year,
                    _dumps(record.modalities),
                    _dumps(record.body_parts),
                    record.gestational_age_weeks,
                    _dumps([c.model_dump() for c in record.concepts]),
                    record.pipeline_version,
                    record.extraction_status,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO token_map (study_token, source_study_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(study_token) DO UPDATE SET
                    source_study_id=excluded.source_study_id
                """,
                (record.study_token, source_study_id, now),
            )
            conn.execute(
                """
                INSERT INTO gateway_meta(key, value) VALUES('index_timestamp', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (now,),
            )

    def list_evidence(self) -> list[ClinicalEvidenceRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM clinical_evidence").fetchall()
        return [self._row_to_evidence(r) for r in rows]

    def get_evidence(self, study_token: str) -> ClinicalEvidenceRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM clinical_evidence WHERE study_token = ?",
                (study_token,),
            ).fetchone()
        return self._row_to_evidence(row) if row else None

    def get_evidence_many(self, tokens: list[str]) -> list[ClinicalEvidenceRecord]:
        if not tokens:
            return []
        placeholders = ",".join("?" for _ in tokens)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM clinical_evidence WHERE study_token IN ({placeholders})",
                tokens,
            ).fetchall()
        by_token = {r["study_token"]: self._row_to_evidence(r) for r in rows}
        return [by_token[t] for t in tokens if t in by_token]

    def count_evidence(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM clinical_evidence").fetchone()
        return int(row["n"])

    def get_index_timestamp(self) -> datetime | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM gateway_meta WHERE key = 'index_timestamp'"
            ).fetchone()
        return _dt(row["value"]) if row else None

    def save_cohort(self, cohort: Cohort) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO cohorts (
                    cohort_handle, query_id, query_fingerprint, member_tokens_json,
                    created_at, expires_at, index_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cohort_handle) DO NOTHING
                """,
                (
                    cohort.cohort_handle,
                    cohort.query_id,
                    cohort.query_fingerprint,
                    _dumps(cohort.member_tokens),
                    cohort.created_at.isoformat(),
                    cohort.expires_at.isoformat(),
                    cohort.index_version,
                ),
            )

    def get_cohort(self, cohort_handle: str) -> Cohort | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM cohorts WHERE cohort_handle = ?",
                (cohort_handle,),
            ).fetchone()
        return self._row_to_cohort(row) if row else None

    def get_cohort_by_fingerprint(self, fingerprint: str) -> Cohort | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM cohorts WHERE query_fingerprint = ? ORDER BY created_at DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
        return self._row_to_cohort(row) if row else None

    def get_organisation(self, organisation_id: str) -> OrganisationPolicy | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM organisations WHERE organisation_id = ?",
                (organisation_id,),
            ).fetchone()
        return self._row_to_org(row) if row else None

    def list_organisations(self) -> list[OrganisationPolicy]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM organisations ORDER BY display_name ASC"
            ).fetchall()
        return [self._row_to_org(r) for r in rows]

    def save_organisation(self, policy: OrganisationPolicy) -> OrganisationPolicy:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO organisations (
                    organisation_id, display_name, status,
                    metadata_auto_approval, data_auto_approval,
                    allowed_metadata_fields_json, allowed_data_fields_json,
                    valid_from, valid_to, policy_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(organisation_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    status=excluded.status,
                    metadata_auto_approval=excluded.metadata_auto_approval,
                    data_auto_approval=excluded.data_auto_approval,
                    allowed_metadata_fields_json=excluded.allowed_metadata_fields_json,
                    allowed_data_fields_json=excluded.allowed_data_fields_json,
                    valid_from=excluded.valid_from,
                    valid_to=excluded.valid_to,
                    policy_version=excluded.policy_version
                """,
                (
                    policy.organisation_id,
                    policy.display_name,
                    policy.status.value,
                    int(policy.metadata_auto_approval),
                    int(policy.data_auto_approval),
                    _dumps(policy.allowed_metadata_fields),
                    _dumps(policy.allowed_data_fields),
                    policy.valid_from.isoformat() if policy.valid_from else None,
                    policy.valid_to.isoformat() if policy.valid_to else None,
                    policy.policy_version,
                ),
            )
        return policy

    def delete_organisation(self, organisation_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM organisations WHERE organisation_id = ?",
                (organisation_id,),
            )
            return cursor.rowcount > 0

    def list_access_requests(
        self,
        status: AccessRequestStatus | None = None,
    ) -> list[AccessRequest]:
        with self.connect() as conn:
            if status is None:
                rows = conn.execute(
                    """
                    SELECT * FROM access_requests
                    ORDER BY created_at DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM access_requests
                    WHERE status = ?
                    ORDER BY created_at DESC
                    """,
                    (status.value,),
                ).fetchall()
            results: list[AccessRequest] = []
            for row in rows:
                events = conn.execute(
                    """
                    SELECT * FROM access_events
                    WHERE provider_request_id = ?
                    ORDER BY id ASC
                    """,
                    (row["provider_request_id"],),
                ).fetchall()
                results.append(self._row_to_access(row, events))
        return results

    def count_access_requests(self, status: AccessRequestStatus) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM access_requests WHERE status = ?",
                (status.value,),
            ).fetchone()
        return int(row["n"])

    def save_access_request(self, request: AccessRequest) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO access_requests (
                    provider_request_id, coordinator_access_request_id, organisation_id,
                    researcher_id, cohort_handle, project_title, purpose,
                    requested_metadata_fields_json, requested_data_fields_json,
                    approved_fields_json, status, approval_path, decision_reason,
                    created_at, updated_at, dataset_id, dataset_preview_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_request_id) DO UPDATE SET
                    organisation_id=excluded.organisation_id,
                    researcher_id=excluded.researcher_id,
                    cohort_handle=excluded.cohort_handle,
                    project_title=excluded.project_title,
                    purpose=excluded.purpose,
                    requested_metadata_fields_json=excluded.requested_metadata_fields_json,
                    requested_data_fields_json=excluded.requested_data_fields_json,
                    approved_fields_json=excluded.approved_fields_json,
                    status=excluded.status,
                    approval_path=excluded.approval_path,
                    decision_reason=excluded.decision_reason,
                    updated_at=excluded.updated_at,
                    dataset_id=excluded.dataset_id,
                    dataset_preview_json=excluded.dataset_preview_json
                """,
                (
                    request.provider_request_id,
                    request.coordinator_access_request_id,
                    request.organisation_id,
                    request.researcher_id,
                    request.cohort_handle,
                    request.project_title,
                    request.purpose,
                    _dumps(request.requested_metadata_fields),
                    _dumps(request.requested_data_fields),
                    _dumps(request.approved_fields),
                    request.status.value,
                    request.approval_path,
                    request.decision_reason,
                    request.created_at.isoformat(),
                    request.updated_at.isoformat(),
                    request.dataset_id,
                    _dumps(request.dataset_preview.model_dump()) if request.dataset_preview else None,
                ),
            )

    def append_access_event(
        self,
        provider_request_id: str,
        event: AccessRequestEvent,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO access_events (
                    provider_request_id, timestamp, from_status, to_status, reason, actor
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_request_id,
                    event.timestamp.isoformat(),
                    event.from_status,
                    event.to_status,
                    event.reason,
                    event.actor,
                ),
            )

    def get_access_request(self, provider_request_id: str) -> AccessRequest | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM access_requests WHERE provider_request_id = ?",
                (provider_request_id,),
            ).fetchone()
            if not row:
                return None
            events = conn.execute(
                """
                SELECT * FROM access_events
                WHERE provider_request_id = ?
                ORDER BY id ASC
                """,
                (provider_request_id,),
            ).fetchall()
        return self._row_to_access(row, events)

    def get_access_request_by_coordinator(
        self, coordinator_access_request_id: str
    ) -> AccessRequest | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM access_requests
                WHERE coordinator_access_request_id = ?
                """,
                (coordinator_access_request_id,),
            ).fetchone()
            if not row:
                return None
            events = conn.execute(
                """
                SELECT * FROM access_events
                WHERE provider_request_id = ?
                ORDER BY id ASC
                """,
                (row["provider_request_id"],),
            ).fetchall()
        return self._row_to_access(row, events)

    def log_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (event_type, payload_json, created_at)
                VALUES (?, ?, ?)
                """,
                (event_type, _dumps(payload), utcnow().isoformat()),
            )

    def _row_to_evidence(self, row: sqlite3.Row) -> ClinicalEvidenceRecord:
        concepts = [CodedConcept(**c) for c in json.loads(row["concepts_json"])]
        keys = set(row.keys())
        gestational = None
        if "gestational_age_weeks" in keys and row["gestational_age_weeks"] is not None:
            gestational = int(row["gestational_age_weeks"])
        return ClinicalEvidenceRecord(
            study_token=row["study_token"],
            provider=row["provider"],
            age_years=float(row["age_years"]),
            age_bucket=row["age_bucket"],
            sex=row["sex"],
            study_year=int(row["study_year"]),
            modalities=list(json.loads(row["modalities_json"])),
            body_parts=list(json.loads(row["body_parts_json"])),
            gestational_age_weeks=gestational,
            concepts=concepts,
            pipeline_version=row["pipeline_version"],
            extraction_status=row["extraction_status"],
        )

    def _row_to_cohort(self, row: sqlite3.Row) -> Cohort:
        return Cohort(
            cohort_handle=row["cohort_handle"],
            query_id=row["query_id"],
            query_fingerprint=row["query_fingerprint"],
            member_tokens=json.loads(row["member_tokens_json"]),
            created_at=_dt(row["created_at"]),  # type: ignore[arg-type]
            expires_at=_dt(row["expires_at"]),  # type: ignore[arg-type]
            index_version=row["index_version"],
        )

    def _row_to_org(self, row: sqlite3.Row) -> OrganisationPolicy:
        return OrganisationPolicy(
            organisation_id=row["organisation_id"],
            display_name=row["display_name"],
            status=OrgStatus(row["status"]),
            metadata_auto_approval=bool(row["metadata_auto_approval"]),
            data_auto_approval=bool(row["data_auto_approval"]),
            allowed_metadata_fields=json.loads(row["allowed_metadata_fields_json"]),
            allowed_data_fields=json.loads(row["allowed_data_fields_json"]),
            valid_from=_dt(row["valid_from"]),
            valid_to=_dt(row["valid_to"]),
            policy_version=row["policy_version"],
        )

    def _row_to_access(
        self, row: sqlite3.Row, event_rows: list[sqlite3.Row]
    ) -> AccessRequest:
        preview = None
        if row["dataset_preview_json"]:
            preview = DatasetPreview(**json.loads(row["dataset_preview_json"]))
        events = [
            AccessRequestEvent(
                timestamp=_dt(e["timestamp"]),  # type: ignore[arg-type]
                from_status=e["from_status"],
                to_status=e["to_status"],
                reason=e["reason"],
                actor=e["actor"],
            )
            for e in event_rows
        ]
        return AccessRequest(
            provider_request_id=row["provider_request_id"],
            coordinator_access_request_id=row["coordinator_access_request_id"],
            organisation_id=row["organisation_id"],
            researcher_id=row["researcher_id"],
            cohort_handle=row["cohort_handle"],
            project_title=row["project_title"],
            purpose=row["purpose"],
            requested_metadata_fields=json.loads(row["requested_metadata_fields_json"]),
            requested_data_fields=json.loads(row["requested_data_fields_json"]),
            approved_fields=json.loads(row["approved_fields_json"]),
            status=AccessRequestStatus(row["status"]),
            approval_path=row["approval_path"],
            decision_reason=row["decision_reason"],
            created_at=_dt(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_dt(row["updated_at"]),  # type: ignore[arg-type]
            dataset_id=row["dataset_id"],
            dataset_preview=preview,
            events=events,
        )


def build_repository(settings: Settings) -> Repository:
    repo = Repository(settings.database_path)
    repo.bootstrap_organisations(settings.bootstrap_orgs)
    return repo
