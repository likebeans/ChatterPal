"""SQLite repository for private learning snapshots and short audio assets."""

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator


DEFAULT_PROFILE = {
    "goal": "daily",
    "daily_minutes": 8,
    "correction_mode": "after_session",
    "retain_audio": False,
    "onboarding_completed": False,
}
RECORD_KINDS = {
    "session",
    "chat",
    "analysis",
    "attempt",
    "review",
    "review_attempt",
    "transcription",
    "event",
}
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
MAX_JSON_BYTES = 1024 * 1024
_TEMP_AUDIO_TTL_SECONDS = 3600
_NO_RESULT = object()
_AUDIO_METADATA = (
    "audio_asset_id, learner_id, record_id, media_type, extension, size_bytes, "
    "sha256, created_at, expires_at, (content IS NOT NULL) AS has_content"
)


class StoreError(Exception):
    """An expected repository error safe to return at the HTTP boundary."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class OperationClaim:
    """Only the worker holding lease_token may complete a pending operation."""

    operation: dict[str, Any]
    lease_token: str | None = None

    @property
    def acquired(self) -> bool:
        return self.lease_token is not None


def _json(value: Any, max_bytes: int = MAX_JSON_BYTES) -> str:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        size = len(text.encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise StoreError(
            "invalid_json", "Data must be finite JSON values", 422
        ) from exc
    if size > max_bytes:
        raise StoreError("data_too_large", "Learning record is too large", 413)
    return text


def _timestamp(value: float) -> str:
    return (
        datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _request_id(value: str) -> None:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise StoreError(
            "invalid_request_id", "request_id must contain 8–128 safe characters", 422
        )


class LearningStore:
    """Open a short-lived connection per call; no process-local identity or lock state."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_audio_bytes: int = 16 * 1024 * 1024,
        audio_retention_days: int = 7,
        clock: Callable[[], float] = time.time,
    ):
        if str(path) == ":memory:":
            raise ValueError("LearningStore requires a durable SQLite file")
        if max_audio_bytes <= 0 or audio_retention_days <= 0:
            raise ValueError("Audio limits must be positive")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.max_audio_bytes = max_audio_bytes
        self.audio_retention_days = audio_retention_days
        self.clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA secure_delete=ON")
        return connection

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        # WAL permits readers during a write. Every schema change is transactional.
        connection = self._connect()
        try:
            if connection.execute("PRAGMA user_version").fetchone()[0] > 2:
                raise RuntimeError(
                    "Learning database schema is newer than this application"
                )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError(
                    "Learning database schema is newer than this application"
                )
            if version == 0:
                statements = (
                    """CREATE TABLE learners (
                        learner_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                        profile_json TEXT NOT NULL, created_at REAL NOT NULL
                    )""",
                    """CREATE TABLE records (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        record_id TEXT NOT NULL UNIQUE,
                        learner_id TEXT NOT NULL REFERENCES learners ON DELETE CASCADE,
                        kind TEXT NOT NULL, session_id TEXT,
                        data_json TEXT NOT NULL, revision INTEGER NOT NULL,
                        created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        UNIQUE(record_id, learner_id)
                    )""",
                    """CREATE INDEX records_owner ON records(learner_id, sequence DESC)""",
                    """CREATE TABLE audio_assets (
                        audio_asset_id TEXT PRIMARY KEY,
                        learner_id TEXT NOT NULL, record_id TEXT NOT NULL,
                        media_type TEXT NOT NULL, extension TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                        content BLOB, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                        FOREIGN KEY(record_id, learner_id)
                            REFERENCES records(record_id, learner_id) ON DELETE CASCADE
                    )""",
                    """CREATE INDEX audio_owner ON audio_assets(learner_id, record_id)""",
                    """CREATE TABLE operations (
                        operation_id TEXT PRIMARY KEY,
                        learner_id TEXT NOT NULL REFERENCES learners ON DELETE CASCADE,
                        request_id TEXT NOT NULL, action TEXT NOT NULL,
                        payload_hash TEXT NOT NULL, status TEXT NOT NULL,
                        session_id TEXT, resource_record_id TEXT,
                        result_type TEXT, result_id TEXT, result_json TEXT,
                        error_json TEXT, lease_token TEXT, lease_until REAL,
                        created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        UNIQUE(learner_id, request_id)
                    )""",
                    """CREATE INDEX operations_owner ON operations(learner_id, status)""",
                )
                for statement in statements:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=1")
            if version < 2:
                connection.execute(
                    "ALTER TABLE records ADD COLUMN parent_record_id TEXT"
                )
                connection.execute(
                    "CREATE INDEX records_parent ON records(learner_id, parent_record_id)"
                )
                connection.execute("ALTER TABLE operations RENAME TO operations_v1")
                connection.execute("DROP INDEX operations_owner")
                connection.execute(
                    """CREATE TABLE operations (
                        operation_id TEXT PRIMARY KEY,
                        learner_id TEXT NOT NULL REFERENCES learners ON DELETE CASCADE,
                        request_id TEXT NOT NULL, action TEXT NOT NULL,
                        payload_hash TEXT NOT NULL, status TEXT NOT NULL,
                        session_id TEXT, resource_record_id TEXT, expected_revision INTEGER,
                        result_type TEXT, result_id TEXT, result_json TEXT,
                        error_json TEXT, lease_token TEXT, lease_until REAL,
                        created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        UNIQUE(learner_id, action, request_id)
                    )"""
                )
                columns = (
                    "operation_id, learner_id, request_id, action, payload_hash, status, "
                    "session_id, resource_record_id, result_type, result_id, result_json, "
                    "error_json, lease_token, lease_until, created_at, updated_at"
                )
                connection.execute(
                    f"INSERT INTO operations ({columns}) SELECT {columns} FROM operations_v1"
                )
                connection.execute("DROP TABLE operations_v1")
                connection.execute(
                    "CREATE INDEX operations_owner ON operations(learner_id, status)"
                )
                connection.execute(
                    """CREATE TABLE operation_records (
                        operation_id TEXT NOT NULL REFERENCES operations ON DELETE CASCADE,
                        record_id TEXT NOT NULL REFERENCES records(record_id) ON DELETE CASCADE,
                        PRIMARY KEY(operation_id, record_id)
                    )"""
                )
                connection.execute(
                    """INSERT INTO operation_records SELECT operation_id, resource_record_id
                       FROM operations WHERE resource_record_id IN (SELECT record_id FROM records)"""
                )
                connection.execute("PRAGMA user_version=2")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        self.path.chmod(0o600)

    def _learner(self, connection: sqlite3.Connection, learner_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM learners WHERE learner_id=?", (learner_id,)
        ).fetchone()
        if row is None:
            raise StoreError(
                "identity_required", "Bootstrap an anonymous identity first", 401
            )
        return row

    def authenticate(self, token: str | None) -> str:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise StoreError(
                "identity_required", "Bootstrap an anonymous identity first", 401
            )
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT learner_id FROM learners WHERE token_hash=?", (digest,)
            ).fetchone()
            if row is None:
                raise StoreError(
                    "identity_required",
                    "Anonymous identity has expired or been deleted",
                    401,
                )
            return row["learner_id"]

    def bootstrap(self, token: str | None = None) -> tuple[dict[str, Any], str]:
        try:
            learner_id = self.authenticate(token)
        except StoreError:
            token = secrets.token_urlsafe(32)
            learner_id = str(uuid.uuid4())
            with self._transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO learners VALUES (?, ?, ?, ?)",
                    (
                        learner_id,
                        hashlib.sha256(token.encode("ascii")).hexdigest(),
                        _json(DEFAULT_PROFILE),
                        self.clock(),
                    ),
                )
        with self._transaction(write=True) as connection:
            row = self._learner(connection, learner_id)
            self._expire_operations(connection, learner_id)
            self._purge_audio(connection, learner_id)
            operations = connection.execute(
                """SELECT * FROM operations WHERE learner_id=?
                   AND status IN ('pending', 'interrupted') ORDER BY created_at""",
                (learner_id,),
            ).fetchall()
            result = {
                "learner_id": learner_id,
                "profile": json.loads(row["profile_json"]),
                "pending_operations": [
                    self._operation(connection, op) for op in operations
                ],
            }
        return result, token

    def get_profile(self, learner_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            return json.loads(self._learner(connection, learner_id)["profile_json"])

    def update_profile(self, learner_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict) or set(patch) - DEFAULT_PROFILE.keys():
            raise StoreError("invalid_profile", "Unknown learning preference", 422)
        with self._transaction(write=True) as connection:
            profile = json.loads(self._learner(connection, learner_id)["profile_json"])
            profile.update(patch)
            if (
                profile["goal"] not in {"daily", "travel", "work"}
                or type(profile["daily_minutes"]) is not int
                or not 1 <= profile["daily_minutes"] <= 120
                or profile["correction_mode"] not in {"immediate", "after_session"}
                or type(profile["retain_audio"]) is not bool
                or type(profile["onboarding_completed"]) is not bool
            ):
                raise StoreError("invalid_profile", "Invalid learning preference", 422)
            connection.execute(
                "UPDATE learners SET profile_json=? WHERE learner_id=?",
                (_json(profile), learner_id),
            )
            if patch.get("retain_audio") is False:
                # Revoke recognition too: a late ASR must not republish cleared audio.
                connection.execute(
                    "UPDATE operations SET status='interrupted', lease_token=NULL, lease_until=NULL, "
                    "error_json=?, updated_at=? WHERE learner_id=? AND action='transcribe' AND status='pending'",
                    (
                        _json(
                            {
                                "code": "audio_cleared",
                                "message": "原音已清除，请重新录音。",
                            }
                        ),
                        self.clock(),
                        learner_id,
                    ),
                )
                connection.execute(
                    "DELETE FROM audio_assets WHERE learner_id=?", (learner_id,)
                )
            return profile

    def _validate_record(
        self, kind: str, session_id: str | None, data: Any, *, transcription=False
    ) -> str:
        if kind == "transcription" and not transcription:
            raise StoreError(
                "business_route_required", "Use the transcription preview route", 403
            )
        if not isinstance(kind, str) or kind not in RECORD_KINDS:
            raise StoreError("invalid_kind", "Unsupported learning record kind", 422)
        if session_id is not None and (
            not isinstance(session_id, str)
            or not session_id.strip()
            or len(session_id) > 128
        ):
            raise StoreError("invalid_session", "Invalid session_id", 422)
        if not isinstance(data, dict):
            raise StoreError(
                "invalid_record", "Learning data must be a JSON object", 422
            )
        return _json(data)

    def _record_row(self, connection, learner_id: str, record_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM records WHERE learner_id=? AND record_id=?",
            (learner_id, record_id),
        ).fetchone()
        if row is None:
            raise StoreError("record_not_found", "Learning record not found", 404)
        return row

    def _record(self, connection, row: sqlite3.Row) -> dict[str, Any]:
        assets = connection.execute(
            f"SELECT {_AUDIO_METADATA} FROM audio_assets WHERE learner_id=? AND record_id=? ORDER BY created_at, audio_asset_id",
            (row["learner_id"], row["record_id"]),
        )
        return {
            "record_id": row["record_id"],
            "kind": row["kind"],
            "session_id": row["session_id"],
            "data": self._refresh_audio(
                connection, row["learner_id"], json.loads(row["data_json"])
            ),
            "revision": row["revision"],
            "created_at": _timestamp(row["created_at"]),
            "updated_at": _timestamp(row["updated_at"]),
            "audio_assets": [self._audio(asset) for asset in assets],
        }

    def get_record(self, learner_id: str, record_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            self._learner(connection, learner_id)
            return self._record(
                connection, self._record_row(connection, learner_id, record_id)
            )

    def list_records(
        self,
        learner_id: str,
        *,
        kind: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError("invalid_limit", "limit must be between 1 and 100", 422)
        if kind is not None and kind not in RECORD_KINDS:
            raise StoreError("invalid_kind", "Unsupported learning record kind", 422)
        sequence = None
        if cursor is not None:
            try:
                if len(cursor) > 512:
                    raise ValueError
                decoded = json.loads(
                    base64.b64decode(cursor, altchars=b"-_", validate=True)
                )
                sequence, cursor_kind, cursor_session = decoded
                if (
                    type(sequence) is not int
                    or sequence <= 0
                    or (cursor_kind, cursor_session) != (kind, session_id)
                ):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError) as exc:
                raise StoreError(
                    "invalid_cursor", "Invalid learning history cursor", 422
                ) from exc
        clauses, values = ["learner_id=?"], [learner_id]
        for name, value in (("kind", kind), ("session_id", session_id)):
            if value is not None:
                clauses.append(f"{name}=?")
                values.append(value)
        if sequence is not None:
            clauses.append("sequence<?")
            values.append(sequence)
        with self._transaction() as connection:
            self._learner(connection, learner_id)
            rows = connection.execute(
                f"SELECT * FROM records WHERE {' AND '.join(clauses)} ORDER BY sequence DESC LIMIT ?",
                (*values, limit + 1),
            ).fetchall()
            items = [self._record(connection, row) for row in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                next_cursor = base64.urlsafe_b64encode(
                    _json([rows[limit - 1]["sequence"], kind, session_id]).encode()
                ).decode("ascii")
            return {"items": items, "next_cursor": next_cursor}

    def _insert_record(
        self, connection, learner_id, kind, session_id, data_json, *, record_id=None
    ):
        record_id, now = record_id or str(uuid.uuid4()), self.clock()
        connection.execute(
            """INSERT INTO records
               (record_id, learner_id, kind, session_id, data_json, revision, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (record_id, learner_id, kind, session_id, data_json, now, now),
        )
        return self._record(
            connection, self._record_row(connection, learner_id, record_id)
        )

    def _fingerprint(self, payload: Any) -> str:
        return hashlib.sha256(
            _json(payload, MAX_JSON_BYTES * 2).encode("utf-8")
        ).hexdigest()

    def _existing_operation(
        self, connection, learner_id, request_id, action, payload_hash
    ):
        row = connection.execute(
            "SELECT * FROM operations WHERE learner_id=? AND action=? AND request_id=?",
            (learner_id, action, request_id),
        ).fetchone()
        if row is not None and (row["action"], row["payload_hash"]) != (
            action,
            payload_hash,
        ):
            raise StoreError(
                "request_conflict",
                "request_id was already used with different input",
                409,
            )
        return row

    def _atomic_mutation(
        self,
        learner_id: str,
        request_id: str,
        action: str,
        payload: Any,
        mutation: Callable,
        result_type: str,
        resource_record_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        _request_id(request_id)
        fingerprint = self._fingerprint(payload)
        with self._transaction(write=True) as connection:
            self._learner(connection, learner_id)
            existing = self._existing_operation(
                connection, learner_id, request_id, action, fingerprint
            )
            if existing is not None:
                if existing["status"] != "succeeded":
                    raise StoreError(
                        "operation_unresolved",
                        "Recover the existing operation before retrying",
                        409,
                    )
                return (
                    self._operation(connection, existing)["result"],
                    existing["operation_id"],
                )
            result = mutation(connection)
            result_id = (
                result.get("record_id")
                if result_type == "record"
                else result.get("audio_asset_id")
            )
            record_id = resource_record_id or result.get("record_id")
            operation_id, now = str(uuid.uuid4()), self.clock()
            connection.execute(
                """INSERT INTO operations
                   (operation_id, learner_id, request_id, action, payload_hash, status,
                    session_id, resource_record_id, result_type, result_id, result_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation_id,
                    learner_id,
                    request_id,
                    action,
                    fingerprint,
                    result.get("session_id"),
                    record_id,
                    result_type,
                    result_id,
                    _json(result, MAX_JSON_BYTES * 2),
                    now,
                    now,
                ),
            )
            self._link_operation_records(
                connection, operation_id, learner_id, result, record_id
            )
            return result, operation_id

    def _link_operation_records(
        self, connection, operation_id, learner_id, result, record_id=None
    ):
        references = {record_id} if record_id else set()

        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get("record_id"), str):
                    references.add(value["record_id"])
                for key in ("created", "updated"):
                    container = value.get(key)
                    if isinstance(container, dict):
                        for item in container.values():
                            collect(item)
                    elif isinstance(container, list):
                        for item in container:
                            collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(result)
        for reference in references:
            self._record_row(connection, learner_id, reference)
            connection.execute(
                "INSERT OR IGNORE INTO operation_records VALUES (?, ?)",
                (operation_id, reference),
            )

    def _update_record(
        self, connection, learner_id, record_id, expected_revision, data
    ):
        if (
            type(expected_revision) is not int
            or expected_revision < 1
            or not isinstance(data, dict)
        ):
            raise StoreError(
                "invalid_record",
                "Provide a JSON object and positive expected_revision",
                422,
            )
        data_json = _json(data)
        row = self._record_row(connection, learner_id, record_id)
        if row["kind"] == "transcription":
            raise StoreError(
                "business_route_required",
                "Transcriptions can only be confirmed or discarded",
                403,
            )
        if row["revision"] != expected_revision:
            raise StoreError(
                "revision_conflict",
                "Learning record was updated by another request",
                409,
            )
        connection.execute(
            "UPDATE records SET data_json=?, revision=revision+1, updated_at=? WHERE record_id=? AND learner_id=?",
            (data_json, self.clock(), record_id, learner_id),
        )
        return self._record(
            connection, self._record_row(connection, learner_id, record_id)
        )

    def _write_records(
        self, connection, learner_id, creates, updates, *, transcription=False
    ):
        if len(creates) + len(updates) > 100:
            raise StoreError(
                "too_many_records", "At most 100 records may be committed at once", 422
            )
        created, updated = {}, []
        for specification in creates:
            key = specification.get("key")
            if not isinstance(key, str) or not key or key in created:
                raise StoreError(
                    "invalid_record_key",
                    "Created records need distinct nonempty keys",
                    422,
                )
            kind, session_id, data = (
                specification.get("kind"),
                specification.get("session_id"),
                specification.get("data"),
            )
            data_json = self._validate_record(
                kind, session_id, data, transcription=transcription
            )
            parent = specification.get("parent_record_id")
            if parent is not None:
                self._record_row(connection, learner_id, parent)
            result = self._insert_record(
                connection,
                learner_id,
                kind,
                session_id,
                data_json,
                record_id=specification.get("record_id"),
            )
            if parent is not None:
                connection.execute(
                    "UPDATE records SET parent_record_id=? WHERE record_id=?",
                    (parent, result["record_id"]),
                )
            created[key] = result
        for specification in updates:
            updated.append(
                self._update_record(
                    connection,
                    learner_id,
                    specification.get("record_id"),
                    specification.get("expected_revision"),
                    specification.get("data"),
                )
            )
        return {"created": created, "updated": updated}

    def commit_records(
        self,
        learner_id: str,
        request_id: str,
        action: str,
        payload: Any,
        *,
        creates: list[dict[str, Any]] | None = None,
        updates: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Persist attempts, paired turns and schedules as one revision-checked result."""
        if not isinstance(action, str) or not 1 <= len(action) <= 128:
            raise ValueError("Invalid operation action")
        creates, updates = creates or [], updates or []
        return self._atomic_mutation(
            learner_id,
            request_id,
            action,
            {"payload": payload, "creates": creates, "updates": updates},
            lambda connection: self._write_records(
                connection, learner_id, creates, updates
            ),
            "records",
        )

    def create_record(
        self,
        learner_id: str,
        request_id: str,
        *,
        kind: str,
        data: dict[str, Any],
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        data_json = self._validate_record(kind, session_id, data)
        return self._atomic_mutation(
            learner_id,
            request_id,
            "create_record",
            {"kind": kind, "session_id": session_id, "data": data},
            lambda connection: self._insert_record(
                connection, learner_id, kind, session_id, data_json
            ),
            "record",
        )

    def update_record(
        self,
        learner_id: str,
        request_id: str,
        record_id: str,
        *,
        expected_revision: int,
        data: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if (
            type(expected_revision) is not int
            or expected_revision < 1
            or not isinstance(data, dict)
        ):
            raise StoreError(
                "invalid_record",
                "Provide a JSON object and positive expected_revision",
                422,
            )
        return self._atomic_mutation(
            learner_id,
            request_id,
            "update_record",
            {
                "record_id": record_id,
                "expected_revision": expected_revision,
                "data": data,
            },
            lambda connection: self._update_record(
                connection, learner_id, record_id, expected_revision, data
            ),
            "record",
            record_id,
        )

    def delete_record(self, learner_id: str, record_id: str) -> None:
        with self._transaction(write=True) as connection:
            self._learner(connection, learner_id)
            row = self._record_row(connection, learner_id, record_id)
            if row["kind"] == "transcription":
                raise StoreError(
                    "business_route_required",
                    "Transcriptions can only be confirmed or discarded",
                    403,
                )
            connection.execute(
                """WITH RECURSIVE descendants(record_id) AS (
                       SELECT record_id FROM records WHERE learner_id=? AND record_id=?
                       UNION ALL
                       SELECT child.record_id FROM records child JOIN descendants parent
                         ON child.parent_record_id=parent.record_id WHERE child.learner_id=?
                   ) DELETE FROM operations WHERE learner_id=? AND (
                       resource_record_id IN (SELECT record_id FROM descendants)
                       OR operation_id IN (SELECT operation_id FROM operation_records
                           WHERE record_id IN (SELECT record_id FROM descendants))
                   )""",
                (learner_id, record_id, learner_id, learner_id),
            )
            connection.execute(
                """WITH RECURSIVE descendants(record_id) AS (
                       SELECT record_id FROM records WHERE learner_id=? AND record_id=?
                       UNION ALL
                       SELECT child.record_id FROM records child JOIN descendants parent
                         ON child.parent_record_id=parent.record_id WHERE child.learner_id=?
                   ) DELETE FROM records WHERE learner_id=? AND record_id IN (SELECT record_id FROM descendants)""",
                (learner_id, record_id, learner_id, learner_id),
            )

    @staticmethod
    def _media(content: bytes) -> tuple[str, str]:
        if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
            return "audio/wav", "wav"
        if content.startswith(b"fLaC"):
            return "audio/flac", "flac"
        if content.startswith(b"OggS"):
            return "audio/ogg", "ogg"
        if content.startswith(b"\x1aE\xdf\xa3"):
            return "audio/webm", "webm"
        if content.startswith(b"ID3") or (
            len(content) >= 2 and content[0] == 255 and content[1] & 0xE0 == 0xE0
        ):
            return "audio/mpeg", "mp3"
        raise StoreError(
            "unsupported_audio", "Upload WAV, MP3, Ogg, FLAC or WebM audio", 415
        )

    def _audio(self, row: sqlite3.Row) -> dict[str, Any]:
        available = row["expires_at"] > self.clock() and bool(row["has_content"])
        return {
            "audio_asset_id": row["audio_asset_id"],
            "record_id": row["record_id"],
            "media_type": row["media_type"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "created_at": _timestamp(row["created_at"]),
            "expires_at": _timestamp(row["expires_at"]),
            "state": "available" if available else "expired",
            "url": (
                f"/api/learning/audio/{row['audio_asset_id']}" if available else None
            ),
        }

    def _purge_audio(self, connection, learner_id):
        connection.execute(
            "UPDATE audio_assets SET content=NULL WHERE learner_id=? AND expires_at<=? AND content IS NOT NULL",
            (learner_id, self.clock()),
        )

    def purge_expired_audio(self) -> int:
        with self._transaction(write=True) as connection:
            cursor = connection.execute(
                "UPDATE audio_assets SET content=NULL WHERE expires_at<=? AND content IS NOT NULL",
                (self.clock(),),
            )
            return cursor.rowcount

    def _validate_audio(self, content: bytes) -> tuple[str, str, str]:
        if not isinstance(content, bytes) or not content:
            raise StoreError("empty_audio", "Audio is empty", 422)
        if len(content) > self.max_audio_bytes:
            raise StoreError(
                "audio_too_large", "Audio asset exceeds the configured limit", 413
            )
        media_type, extension = self._media(content)
        return media_type, extension, hashlib.sha256(content).hexdigest()

    def _insert_audio(
        self, connection, learner_id, record_id, content, details, *, retain
    ):
        profile = json.loads(self._learner(connection, learner_id)["profile_json"])
        self._record_row(connection, learner_id, record_id)
        if retain and not profile["retain_audio"]:
            raise StoreError(
                "audio_retention_disabled",
                "Enable audio retention before saving audio",
                409,
            )
        self._purge_audio(connection, learner_id)
        media_type, extension, digest = details
        asset_id, now = str(uuid.uuid4()), self.clock()
        lifetime = (
            self.audio_retention_days * 86400 if retain else _TEMP_AUDIO_TTL_SECONDS
        )
        connection.execute(
            "INSERT INTO audio_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                learner_id,
                record_id,
                media_type,
                extension,
                len(content),
                digest,
                content,
                now,
                now + lifetime,
            ),
        )
        return self._audio(
            connection.execute(
                f"SELECT {_AUDIO_METADATA} FROM audio_assets WHERE audio_asset_id=?",
                (asset_id,),
            ).fetchone()
        )

    def add_audio(
        self,
        learner_id: str,
        request_id: str,
        record_id: str,
        content: bytes,
    ) -> tuple[dict[str, Any], str]:
        details = self._validate_audio(content)
        return self._atomic_mutation(
            learner_id,
            request_id,
            "add_audio",
            {"record_id": record_id, "sha256": details[2]},
            lambda connection: self._insert_audio(
                connection, learner_id, record_id, content, details, retain=True
            ),
            "audio",
            record_id,
        )

    def get_audio(self, learner_id: str, asset_id: str) -> tuple[dict[str, Any], bytes]:
        with self._transaction() as connection:
            self._learner(connection, learner_id)
            row = connection.execute(
                f"SELECT {_AUDIO_METADATA} FROM audio_assets WHERE learner_id=? AND audio_asset_id=?",
                (learner_id, asset_id),
            ).fetchone()
            if row is None:
                raise StoreError("audio_not_found", "Audio asset not found", 404)
            metadata = self._audio(row)
            if metadata["state"] != "available":
                raise StoreError("audio_expired", "Audio asset has expired", 404)
            content = connection.execute(
                "SELECT content FROM audio_assets WHERE learner_id=? AND audio_asset_id=?",
                (learner_id, asset_id),
            ).fetchone()[0]
            return metadata, bytes(content)

    def _refresh_audio(self, connection, learner_id, result):
        if isinstance(result, list):
            return [
                self._refresh_audio(connection, learner_id, item) for item in result
            ]
        if not isinstance(result, dict):
            return result
        refreshed = {
            key: self._refresh_audio(connection, learner_id, value)
            for key, value in result.items()
        }
        asset_id = result.get("audio_asset_id")
        if not isinstance(asset_id, str):
            return refreshed
        row = connection.execute(
            f"SELECT {_AUDIO_METADATA} FROM audio_assets WHERE learner_id=? AND audio_asset_id=?",
            (learner_id, asset_id),
        ).fetchone()
        if row is not None:
            metadata = self._audio(row)
            refreshed.update(
                {
                    key: metadata[key]
                    for key in ("state", "url", "record_id", "expires_at")
                }
            )
        elif result.get("url") == f"/api/learning/audio/{asset_id}" or result.get(
            "state"
        ) in ("available", "expired", "not_saved", "unavailable"):
            # Missing private assets (deleted, disabled or foreign) never retain a URL.
            # Opaque business fields with the same name remain unchanged.
            refreshed.update(state="not_saved", url=None)
        return refreshed

    def _operation(self, connection, row) -> dict[str, Any]:
        result = (
            self._refresh_audio(
                connection, row["learner_id"], json.loads(row["result_json"])
            )
            if row["result_json"]
            else None
        )
        if row["result_type"] == "transcription" and result is not None:
            draft = self._record_row(connection, row["learner_id"], row["result_id"])
            data = json.loads(draft["data_json"])
            result["status"] = data["status"]
            if data["status"] == "ready" and (
                data["expires_epoch"] <= self.clock()
                or result["audio"]["state"] != "available"
            ):
                result["status"] = "expired"
        return {
            "operation_id": row["operation_id"],
            "request_id": row["request_id"],
            "status": row["status"],
            "session_id": row["session_id"],
            "result_type": row["result_type"],
            "result_id": row["result_id"],
            "result": result,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
        }

    def _expire_operations(self, connection, learner_id):
        connection.execute(
            """UPDATE operations SET status='interrupted', lease_token=NULL, lease_until=NULL,
               error_json=?, updated_at=?
               WHERE learner_id=? AND status='pending' AND lease_until<=?""",
            (
                _json(
                    {
                        "code": "operation_interrupted",
                        "message": "Worker lease expired; recover before retrying",
                    }
                ),
                self.clock(),
                learner_id,
                self.clock(),
            ),
        )

    def get_operation(
        self,
        learner_id: str,
        *,
        operation_id: str | None = None,
        request_id: str | None = None,
        action: str | None = None,
    ) -> dict[str, Any]:
        if (operation_id is None) == (request_id is None):
            raise ValueError("Provide operation_id or request_id")
        with self._transaction(write=True) as connection:
            self._learner(connection, learner_id)
            self._expire_operations(connection, learner_id)
            key, value = (
                ("operation_id", operation_id)
                if operation_id is not None
                else ("request_id", request_id)
            )
            query, parameters = (
                f"SELECT * FROM operations WHERE learner_id=? AND {key}=?",
                [learner_id, value],
            )
            if action is not None:
                query += " AND action=?"
                parameters.append(action)
            rows = connection.execute(query, parameters).fetchall()
            if not rows:
                raise StoreError(
                    "operation_not_found", "Learning operation not found", 404
                )
            if len(rows) > 1:
                raise StoreError(
                    "ambiguous_request",
                    "Specify action for this reused request_id",
                    409,
                )
            return self._operation(connection, rows[0])

    def current_pending(self, learner_id: str, session_id: str) -> list[dict[str, Any]]:
        """Inspect active session work after expiring leases in the same transaction."""
        with self._transaction(write=True) as connection:
            self._learner(connection, learner_id)
            self._expire_operations(connection, learner_id)
            rows = connection.execute(
                """SELECT * FROM operations WHERE learner_id=? AND session_id=?
                   AND status='pending' ORDER BY created_at, operation_id""",
                (learner_id, session_id),
            ).fetchall()
            return [self._operation(connection, row) for row in rows]

    def begin_operation(
        self,
        learner_id: str,
        request_id: str,
        action: str,
        payload: Any,
        *,
        session_id: str | None = None,
        lease_seconds: int = 300,
        record_id: str | None = None,
        expected_revision: int | None = None,
        exclusive_session: bool = False,
        transcription_consume: dict[str, Any] | None = None,
    ) -> OperationClaim:
        _request_id(request_id)
        if (
            not isinstance(action, str)
            or not 1 <= len(action) <= 128
            or not 1 <= lease_seconds <= 3600
        ):
            raise ValueError("Invalid operation action or lease")
        if expected_revision is not None and (
            record_id is None
            or type(expected_revision) is not int
            or expected_revision < 1
        ):
            raise ValueError(
                "expected_revision requires a record_id and positive revision"
            )
        fingerprint = self._fingerprint(
            {
                "payload": payload,
                "record_id": record_id,
                "expected_revision": expected_revision,
            }
        )
        with self._transaction(write=True) as connection:
            self._learner(connection, learner_id)
            self._expire_operations(connection, learner_id)
            existing = self._existing_operation(
                connection, learner_id, request_id, action, fingerprint
            )
            if existing is not None:
                return OperationClaim(self._operation(connection, existing))
            if exclusive_session and session_id is not None:
                pending = connection.execute(
                    """SELECT 1 FROM operations WHERE learner_id=? AND session_id=?
                       AND status='pending' LIMIT 1""",
                    (learner_id, session_id),
                ).fetchone()
                if pending is not None:
                    raise StoreError(
                        "session_busy", "A learning operation is still pending", 409
                    )
            if record_id is not None:
                row = self._record_row(connection, learner_id, record_id)
                if (
                    expected_revision is not None
                    and row["revision"] != expected_revision
                ):
                    raise StoreError(
                        "revision_conflict",
                        "Learning record was updated by another request",
                        409,
                    )
            if transcription_consume is not None:
                self._checked_transcription(
                    connection, learner_id, transcription_consume, record_id
                )
            operation_id, lease_token, now = (
                str(uuid.uuid4()),
                secrets.token_urlsafe(32),
                self.clock(),
            )
            connection.execute(
                """INSERT INTO operations
                   (operation_id, learner_id, request_id, action, payload_hash, status,
                    session_id, resource_record_id, expected_revision, lease_token, lease_until, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation_id,
                    learner_id,
                    request_id,
                    action,
                    fingerprint,
                    session_id,
                    record_id,
                    expected_revision,
                    lease_token,
                    now + lease_seconds,
                    now,
                    now,
                ),
            )
            if transcription_consume is not None:
                self._link_operation_records(
                    connection,
                    operation_id,
                    learner_id,
                    None,
                    transcription_consume["transcription_id"],
                )
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            return OperationClaim(self._operation(connection, row), lease_token)

    def _owned_claim(self, connection, learner_id, operation_id, lease_token):
        self._learner(connection, learner_id)
        row = connection.execute(
            "SELECT * FROM operations WHERE learner_id=? AND operation_id=?",
            (learner_id, operation_id),
        ).fetchone()
        if row is None:
            raise StoreError("operation_not_found", "Learning operation not found", 404)
        if (
            row["status"] != "pending"
            or not isinstance(lease_token, str)
            or not secrets.compare_digest(row["lease_token"] or "", lease_token)
            or row["lease_until"] <= self.clock()
        ):
            raise StoreError(
                "operation_lease_lost", "Operation lease is no longer valid", 409
            )
        return row

    def validate_operation_claim(self, learner_id, operation_id, lease_token):
        """Check durable revocation before a queued worker starts provider work."""
        with self._transaction() as connection:
            self._owned_claim(connection, learner_id, operation_id, lease_token)

    def _checked_transcription(self, connection, learner_id, specification, source_id):
        row = self._record_row(
            connection, learner_id, specification["transcription_id"]
        )
        if row["kind"] != "transcription":
            raise StoreError("transcription_not_found", "找不到这份录音转写。", 404)
        data = json.loads(row["data_json"])
        if data["status"] != "ready":
            raise StoreError(
                "transcription_consumed", "这份录音已经提交，请录制新的回答。", 409
            )
        if data["expires_epoch"] <= self.clock():
            raise StoreError(
                "transcription_expired", "录音核对已到期，请重新录音。", 410
            )
        if (
            data["source_record_id"] != source_id
            or data["context"] != specification["context"]
        ):
            raise StoreError(
                "transcription_context_changed",
                "这份录音不属于当前题目或练习版本，请重新录音。",
                409,
            )
        source = self._record_row(connection, learner_id, source_id)
        if source["revision"] != data["source_revision"]:
            raise StoreError(
                "revision_conflict", "练习版本已变化，请刷新后重新录音。", 409
            )
        asset = connection.execute(
            "SELECT * FROM audio_assets WHERE learner_id=? AND audio_asset_id=?",
            (learner_id, data["audio"]["audio_asset_id"]),
        ).fetchone()
        if (
            asset is None
            or asset["content"] is None
            or asset["expires_at"] <= self.clock()
        ):
            raise StoreError(
                "transcription_expired", "原音已到期或已清除，请重新录音。", 410
            )
        if (
            asset["record_id"] != row["record_id"]
            or asset["sha256"] != data["audio_sha256"]
        ):
            raise StoreError("invalid_transcription", "无法核对原音，请重新录音。", 409)
        return row, data, asset

    def get_transcription(self, learner_id, transcription_id):
        with self._transaction() as connection:
            self._learner(connection, learner_id)
            row = self._record_row(connection, learner_id, transcription_id)
            if row["kind"] != "transcription":
                raise StoreError("transcription_not_found", "找不到这份录音转写。", 404)
            data = self._refresh_audio(
                connection, learner_id, json.loads(row["data_json"])
            )
            status = data["status"]
            if status == "ready" and (
                data["expires_epoch"] <= self.clock()
                or data["audio"]["state"] != "available"
            ):
                status = "expired"
            return {
                key: data[key]
                for key in (
                    "transcription_id",
                    "context",
                    "machine_transcript",
                    "audio",
                    "received_at",
                    "expires_at",
                )
            } | {"status": status}

    def discard_transcription_request(self, learner_id, request_id):
        with self._transaction(write=True) as connection:
            self._learner(connection, learner_id)
            operation = connection.execute(
                "SELECT * FROM operations WHERE learner_id=? AND request_id=? AND action='transcribe'",
                (learner_id, request_id),
            ).fetchone()
            if operation is None:
                raise StoreError("operation_not_found", "找不到这次识别操作。", 404)
            record_id = operation["result_id"]
            if record_id is not None:
                row = self._record_row(connection, learner_id, record_id)
                if row["kind"] != "transcription":
                    raise StoreError(
                        "transcription_not_found", "找不到这份录音转写。", 404
                    )
                if json.loads(row["data_json"])["status"] == "consumed":
                    raise StoreError(
                        "transcription_consumed", "这份录音已保存为回答。", 409
                    )
                # Reuse the same deletion relations as normal source deletion.
                connection.execute(
                    "DELETE FROM operations WHERE learner_id=? AND operation_id IN "
                    "(SELECT operation_id FROM operation_records WHERE record_id=?)",
                    (learner_id, record_id),
                )
                connection.execute(
                    "DELETE FROM records WHERE learner_id=? AND record_id=?",
                    (learner_id, record_id),
                )
            else:
                # Removing a pending claim prevents any late worker publication.
                connection.execute(
                    "DELETE FROM operations WHERE learner_id=? AND operation_id=?",
                    (learner_id, operation["operation_id"]),
                )

    @staticmethod
    def _resolve_audio_keys(value, assets):
        if isinstance(value, list):
            return [LearningStore._resolve_audio_keys(item, assets) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"audio_key"}:
            key = value["audio_key"]
            if not isinstance(key, str) or key not in assets:
                raise StoreError(
                    "invalid_audio_key", "Audio placeholder has no matching asset", 422
                )
            return dict(assets[key])
        return {
            key: LearningStore._resolve_audio_keys(item, assets)
            for key, item in value.items()
        }

    def _complete_audio(
        self, connection, learner_id, target, writes, audio, references=None
    ):
        if not isinstance(audio, list) or len(audio) > 100:
            raise StoreError(
                "invalid_audio", "Provide at most 100 keyed audio assets", 422
            )
        assets, prepared = {}, []
        keys = set()
        for specification in audio:
            if not isinstance(specification, dict):
                raise StoreError("invalid_audio", "Audio asset must be an object", 422)
            key, retain = specification.get("key"), specification.get("retain")
            if not isinstance(key, str) or not key or key in keys:
                raise StoreError(
                    "invalid_audio_key", "Audio assets need distinct nonempty keys", 422
                )
            if type(retain) is not bool:
                raise StoreError("invalid_audio", "Audio retain must be a boolean", 422)
            content = specification.get("content")
            prepared.append((key, content, retain, self._validate_audio(content)))
            keys.add(key)
        if prepared:
            if target is None and len(writes["created"]) == 1:
                target = next(iter(writes["created"].values()))["record_id"]
            if target is None:
                raise StoreError(
                    "invalid_audio_record",
                    "Audio needs a claimed, explicit or newly created source record",
                    422,
                )
            self._record_row(connection, learner_id, target)
            for key, content, retain, details in prepared:
                assets[key] = self._insert_audio(
                    connection, learner_id, target, content, details, retain=retain
                )

        assets.update(references or {})

        # Resolve persisted snapshots without incrementing their revision a second time.
        # All rows and blobs remain uncommitted until the operation result is valid.
        def resolve_record(item):
            row = self._record_row(connection, learner_id, item["record_id"])
            data = self._resolve_audio_keys(json.loads(row["data_json"]), assets)
            connection.execute(
                "UPDATE records SET data_json=? WHERE learner_id=? AND record_id=?",
                (_json(data), learner_id, item["record_id"]),
            )
            return self._record(
                connection, self._record_row(connection, learner_id, item["record_id"])
            )

        return {
            "created": {
                key: resolve_record(item) for key, item in writes["created"].items()
            },
            "updated": [resolve_record(item) for item in writes["updated"]],
        }, assets

    def complete_operation(
        self,
        learner_id: str,
        operation_id: str,
        lease_token: str,
        *,
        result: Any = _NO_RESULT,
        result_type: str | None = None,
        result_id: str | None = None,
        record: dict[str, Any] | None = None,
        resource_record_id: str | None = None,
        creates: list[dict[str, Any]] | None = None,
        updates: list[dict[str, Any]] | None = None,
        audio: list[dict[str, Any]] | None = None,
        transcription_consume: dict[str, Any] | None = None,
        transcription_create: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit owned writes and optional audio with their HTTP result atomically.

        A supplied result is the business response, including an explicit None.
        Without it, preserve A2's record/compound-write result. With audio supplied,
        exact {"audio_key": key} objects in data and result become asset metadata.
        retain=False briefly stores a private SQLite BLOB for one hour; retain=True
        requires the current profile's opt-in and uses the configured retention.
        """
        with self._transaction(write=True) as connection:
            claim = self._owned_claim(connection, learner_id, operation_id, lease_token)
            if claim["resource_record_id"] is not None:
                source = self._record_row(
                    connection, learner_id, claim["resource_record_id"]
                )
                if (
                    claim["expected_revision"] is not None
                    and source["revision"] != claim["expected_revision"]
                ):
                    raise StoreError(
                        "revision_conflict",
                        "Learning record was updated by another request",
                        409,
                    )
            if transcription_create is not None and claim["action"] != "transcribe":
                raise StoreError(
                    "business_route_required",
                    "Use the transcription preview route",
                    403,
                )
            references = {}
            if (
                transcription_create is not None
                and transcription_create["expires_epoch"] <= self.clock()
            ):
                raise StoreError(
                    "transcription_expired", "录音核对已到期，请重新录音。", 410
                )
            if transcription_consume is not None:
                draft_row, draft, asset = self._checked_transcription(
                    connection,
                    learner_id,
                    transcription_consume,
                    claim["resource_record_id"],
                )
                profile = json.loads(
                    self._learner(connection, learner_id)["profile_json"]
                )
                expires = (
                    self.clock() + self.audio_retention_days * 86400
                    if profile["retain_audio"]
                    else asset["expires_at"]
                )
                connection.execute(
                    "UPDATE audio_assets SET record_id=?, expires_at=? WHERE learner_id=? AND audio_asset_id=?",
                    (
                        claim["resource_record_id"],
                        expires,
                        learner_id,
                        asset["audio_asset_id"],
                    ),
                )
                metadata = connection.execute(
                    f"SELECT {_AUDIO_METADATA} FROM audio_assets WHERE learner_id=? AND audio_asset_id=?",
                    (learner_id, asset["audio_asset_id"]),
                ).fetchone()
                references["transcription"] = self._audio(metadata)
                draft["status"] = "consumed"
                draft["submission_operation_id"] = operation_id
                connection.execute(
                    "UPDATE records SET data_json=?, revision=revision+1, updated_at=? WHERE learner_id=? AND record_id=?",
                    (_json(draft), self.clock(), learner_id, draft_row["record_id"]),
                )
            supplied_result = result is not _NO_RESULT
            compound = creates is not None or updates is not None
            writes = {"created": {}, "updated": []}
            if compound:
                if record is not None:
                    raise ValueError("Use compound records or a single record")
                writes = self._write_records(
                    connection,
                    learner_id,
                    creates or [],
                    updates or [],
                    transcription=transcription_create is not None,
                )
                result_type = result_type or "records"
            if record is not None:
                data_json = self._validate_record(
                    record.get("kind"), record.get("session_id"), record.get("data")
                )
                created = self._insert_record(
                    connection,
                    learner_id,
                    record["kind"],
                    record.get("session_id"),
                    data_json,
                )
                writes["created"]["record"] = created
                resource_record_id = created["record_id"]
                if not supplied_result or result_id is None:
                    result_id = created["record_id"]
                result_type = result_type or "record"
            assets = {}
            if audio is not None or references:
                writes, assets = self._complete_audio(
                    connection,
                    learner_id,
                    resource_record_id or claim["resource_record_id"],
                    writes,
                    audio if audio is not None else [],
                    references,
                )
            if not supplied_result:
                if record is not None:
                    result = writes["created"]["record"]
                else:
                    result = writes if compound else None
            elif audio is not None or references:
                result = self._resolve_audio_keys(
                    json.loads(_json(result, MAX_JSON_BYTES * 2)), assets
                )
            resource_record_id = resource_record_id or claim["resource_record_id"]
            if resource_record_id is not None:
                self._record_row(connection, learner_id, resource_record_id)
            result_json = _json(result, MAX_JSON_BYTES * 2)
            # The HTTP result is opaque business data. Only repository writes and
            # explicitly bound source records define relations used for deletion.
            self._link_operation_records(
                connection, operation_id, learner_id, writes, resource_record_id
            )
            if claim["resource_record_id"] is not None:
                self._link_operation_records(
                    connection,
                    operation_id,
                    learner_id,
                    None,
                    claim["resource_record_id"],
                )
            # Audio and snapshot work may cross the lease deadline. Recheck before
            # publication so expired workers roll back their records and BLOBs too.
            self._owned_claim(connection, learner_id, operation_id, lease_token)
            connection.execute(
                """UPDATE operations SET status='succeeded', result_json=?, result_type=?,
                   result_id=?, resource_record_id=?, lease_token=NULL, lease_until=NULL, updated_at=?
                   WHERE operation_id=? AND learner_id=?""",
                (
                    result_json,
                    result_type,
                    result_id,
                    resource_record_id,
                    self.clock(),
                    operation_id,
                    learner_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            operation = self._operation(connection, row)
            # A transaction can outlive the lease while preparing audio or snapshots.
            # Check immediately before commit so every associated write rolls back.
            if (
                transcription_consume is not None
                and draft["expires_epoch"] <= self.clock()
            ):
                raise StoreError(
                    "transcription_expired", "录音核对已到期，请重新录音。", 410
                )
            if (
                transcription_create is not None
                and transcription_create["expires_epoch"] <= self.clock()
            ):
                raise StoreError(
                    "transcription_expired", "录音核对已到期，请重新录音。", 410
                )
            if claim["lease_until"] <= self.clock():
                raise StoreError(
                    "operation_lease_lost", "Operation lease is no longer valid", 409
                )
            return operation

    def interrupt_operations(self, operation_ids: list[str]) -> int:
        """A runtime interrupts only its own operations during shutdown/recovery."""
        count = 0
        with self._transaction(write=True) as connection:
            for operation_id in operation_ids:
                changed = connection.execute(
                    """UPDATE operations SET status='interrupted', lease_token=NULL,
                       lease_until=NULL, error_json=?, updated_at=?
                       WHERE operation_id=? AND status='pending'""",
                    (
                        _json(
                            {
                                "code": "operation_interrupted",
                                "message": "Worker stopped before completion",
                            }
                        ),
                        self.clock(),
                        operation_id,
                    ),
                )
                count += changed.rowcount
        return count

    def fail_operation(
        self,
        learner_id: str,
        operation_id: str,
        lease_token: str,
        *,
        code: str = "operation_failed",
        message: str = "Learning operation failed",
    ) -> dict[str, Any]:
        with self._transaction(write=True) as connection:
            self._owned_claim(connection, learner_id, operation_id, lease_token)
            connection.execute(
                """UPDATE operations SET status='failed', error_json=?, lease_token=NULL,
                   lease_until=NULL, updated_at=? WHERE learner_id=? AND operation_id=?""",
                (
                    _json({"code": code, "message": message}),
                    self.clock(),
                    learner_id,
                    operation_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            return self._operation(connection, row)

    def export_data(self, learner_id: str) -> BinaryIO:
        """Return a consistent JSON snapshot without binary audio or credentials."""
        output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False)

        def write(value):
            for chunk in encoder.iterencode(value):
                output.write(chunk.encode("utf-8"))

        try:
            with self._transaction() as connection:
                learner = self._learner(connection, learner_id)
                header = {
                    "schema_version": 1,
                    "exported_at": _timestamp(self.clock()),
                    "learner": {
                        "learner_id": learner_id,
                        "profile": json.loads(learner["profile_json"]),
                        "created_at": _timestamp(learner["created_at"]),
                    },
                }
                write(header)
                output.seek(-1, 1)
                for name, table, ordering, serialize in (
                    ("records", "records", "sequence", self._record),
                    ("operations", "operations", "created_at", self._operation),
                ):
                    output.write(f',"{name}":['.encode())
                    separator = b""
                    for row in connection.execute(
                        f"SELECT * FROM {table} WHERE learner_id=? ORDER BY {ordering}",
                        (learner_id,),
                    ):
                        output.write(separator)
                        write(serialize(connection, row))
                        separator = b","
                    output.write(b"]")
                output.write(b"}")
            output.seek(0)
            return output
        except BaseException:
            output.close()
            raise

    def delete_learner(self, learner_id: str) -> None:
        with self._transaction(write=True) as connection:
            self._learner(connection, learner_id)
            connection.execute("DELETE FROM learners WHERE learner_id=?", (learner_id,))
