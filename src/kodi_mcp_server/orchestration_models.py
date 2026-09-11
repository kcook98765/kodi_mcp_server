"""Internal outcome and structured audit primitives for future mutations."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

_SAFE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,63})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


class MutationOutcomeStatus(str, Enum):
    PREPARED = "prepared"
    ALREADY_CURRENT = "already_current"
    MUTATION_NOT_ATTEMPTED = "mutation_not_attempted"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_ABSENT = "verified_absent"
    VERIFIED_MISMATCH = "verified_mismatch"
    AMBIGUOUS_AFTER_MUTATION = "ambiguous_after_mutation"
    ROLLBACK_SUCCEEDED = "rollback_succeeded"
    ROLLBACK_FAILED = "rollback_failed"


_ALLOWED_OUTCOME_FLAGS = {
    MutationOutcomeStatus.PREPARED: {(False, False)},
    MutationOutcomeStatus.ALREADY_CURRENT: {(False, True)},
    MutationOutcomeStatus.MUTATION_NOT_ATTEMPTED: {(False, False)},
    MutationOutcomeStatus.VERIFIED_SUCCESS: {(True, True)},
    MutationOutcomeStatus.VERIFIED_ABSENT: {(False, True), (True, True)},
    MutationOutcomeStatus.VERIFIED_MISMATCH: {(False, True), (True, True)},
    MutationOutcomeStatus.AMBIGUOUS_AFTER_MUTATION: {(True, False)},
    MutationOutcomeStatus.ROLLBACK_SUCCEEDED: {(True, True)},
    MutationOutcomeStatus.ROLLBACK_FAILED: {(True, False), (True, True)},
}


def _validate_outcome_state(status, mutation_attempted, readback_verified) -> None:
    if not isinstance(status, MutationOutcomeStatus):
        raise ValueError("status must be MutationOutcomeStatus")
    if not isinstance(mutation_attempted, bool) or not isinstance(readback_verified, bool):
        raise ValueError("outcome flags must be booleans")
    if (mutation_attempted, readback_verified) not in _ALLOWED_OUTCOME_FLAGS[status]:
        raise ValueError("invalid outcome combination")


@dataclass(frozen=True)
class MutationOutcome:
    status: MutationOutcomeStatus
    mutation_attempted: bool
    readback_verified: bool

    def __post_init__(self) -> None:
        _validate_outcome_state(
            self.status, self.mutation_attempted, self.readback_verified
        )

    @classmethod
    def prepared(cls) -> "MutationOutcome":
        return cls(
            status=MutationOutcomeStatus.PREPARED,
            mutation_attempted=False,
            readback_verified=False,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "mutation_attempted": self.mutation_attempted,
            "readback_verified": self.readback_verified,
        }


@dataclass(frozen=True)
class MutationAuditRecord:
    operation_id: str
    mutation_domain_id: str
    target_id: str
    operation: str
    artifact_sha256: str
    size_bytes: int
    started_at: str
    ended_at: str | None
    mutation_attempted: bool
    readback_verified: bool
    outcome: MutationOutcomeStatus

    def __post_init__(self) -> None:
        try:
            operation_uuid = uuid.UUID(self.operation_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("operation_id must be a canonical UUID") from exc
        if str(operation_uuid) != self.operation_id:
            raise ValueError("operation_id must be a canonical UUID")
        for label, value in (
            ("mutation_domain_id", self.mutation_domain_id),
            ("target_id", self.target_id),
            ("operation", self.operation),
        ):
            if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
                raise ValueError(f"{label} must be a safe bounded identifier")
        if not isinstance(self.artifact_sha256, str) or not _SHA256_RE.fullmatch(
            self.artifact_sha256
        ):
            raise ValueError("artifact_sha256 must be lowercase SHA-256 hex")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes must be a nonnegative integer")
        if not isinstance(self.started_at, str) or not _TIMESTAMP_RE.fullmatch(
            self.started_at
        ):
            raise ValueError("started_at must be a bounded UTC timestamp")
        started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        if self.ended_at is not None:
            if not isinstance(self.ended_at, str) or not _TIMESTAMP_RE.fullmatch(
                self.ended_at
            ):
                raise ValueError("ended_at must be a bounded UTC timestamp or null")
            ended = datetime.fromisoformat(self.ended_at.replace("Z", "+00:00"))
            if ended < started:
                raise ValueError("ended_at must not precede started_at")
        if not isinstance(self.mutation_attempted, bool) or not isinstance(
            self.readback_verified, bool
        ):
            raise ValueError("audit verification flags must be booleans")
        if not isinstance(self.outcome, MutationOutcomeStatus):
            raise ValueError("outcome must be MutationOutcomeStatus")
        _validate_outcome_state(
            self.outcome, self.mutation_attempted, self.readback_verified
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "mutation_domain_id": self.mutation_domain_id,
            "target_id": self.target_id,
            "operation": self.operation,
            "artifact_sha256": self.artifact_sha256,
            "size_bytes": self.size_bytes,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "mutation_attempted": self.mutation_attempted,
            "readback_verified": self.readback_verified,
            "outcome": self.outcome.value,
        }


def emit_mutation_audit(logger: logging.Logger, record: MutationAuditRecord) -> None:
    """Emit one bounded JSON audit event without paths, endpoints, or credentials."""

    logger.info(
        "orchestration_mutation_audit %s",
        json.dumps(record.to_dict(), sort_keys=True),
    )
