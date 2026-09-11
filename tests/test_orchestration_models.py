import json
import logging

import pytest

from kodi_mcp_server.orchestration_models import (
    MutationAuditRecord,
    MutationOutcome,
    MutationOutcomeStatus,
    emit_mutation_audit,
)


def test_mutation_outcome_status_vocabulary_is_complete_and_stable():
    assert [status.value for status in MutationOutcomeStatus] == [
        "prepared",
        "already_current",
        "mutation_not_attempted",
        "verified_success",
        "verified_absent",
        "verified_mismatch",
        "ambiguous_after_mutation",
        "rollback_succeeded",
        "rollback_failed",
    ]


def test_prepared_outcome_is_internal_structured_state():
    outcome = MutationOutcome.prepared()
    assert outcome.to_dict() == {
        "status": "prepared",
        "mutation_attempted": False,
        "readback_verified": False,
    }


@pytest.mark.parametrize(
    ("status", "attempted", "verified"),
    [
        (MutationOutcomeStatus.VERIFIED_SUCCESS, False, True),
        (MutationOutcomeStatus.VERIFIED_ABSENT, True, False),
        (MutationOutcomeStatus.VERIFIED_MISMATCH, True, False),
        (MutationOutcomeStatus.AMBIGUOUS_AFTER_MUTATION, False, False),
        (MutationOutcomeStatus.ROLLBACK_SUCCEEDED, False, True),
        (MutationOutcomeStatus.PREPARED, True, False),
        (MutationOutcomeStatus.ALREADY_CURRENT, True, True),
    ],
)
def test_mutation_outcome_rejects_impossible_status_combinations(
    status, attempted, verified
):
    with pytest.raises(ValueError, match="outcome combination"):
        MutationOutcome(status, attempted, verified)


@pytest.mark.parametrize("field", ["mutation_attempted", "readback_verified"])
def test_mutation_outcome_rejects_non_boolean_flags(field):
    values = {
        "status": MutationOutcomeStatus.PREPARED,
        "mutation_attempted": False,
        "readback_verified": False,
    }
    values[field] = 0
    with pytest.raises(ValueError, match="booleans"):
        MutationOutcome(**values)


def test_mutation_outcome_rejects_invalid_status_string():
    with pytest.raises(ValueError, match="MutationOutcomeStatus"):
        MutationOutcome("verified_success", True, True)


def test_audit_record_contains_only_safe_structured_mutation_fields(caplog):
    record = MutationAuditRecord(
        operation_id="11111111-1111-4111-8111-111111111111",
        mutation_domain_id="bridge-0123456789abcdef0123456789abcdef",
        target_id="living-room",
        operation="repo_stage_snapshot",
        artifact_sha256="a" * 64,
        size_bytes=1234,
        started_at="2026-09-10T12:00:00Z",
        ended_at="2026-09-10T12:00:01Z",
        mutation_attempted=False,
        readback_verified=False,
        outcome=MutationOutcomeStatus.PREPARED,
    )

    with caplog.at_level(logging.INFO, logger="audit-test"):
        emit_mutation_audit(logging.getLogger("audit-test"), record)

    payload = record.to_dict()
    assert payload == {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "mutation_domain_id": "bridge-0123456789abcdef0123456789abcdef",
        "target_id": "living-room",
        "operation": "repo_stage_snapshot",
        "artifact_sha256": "a" * 64,
        "size_bytes": 1234,
        "started_at": "2026-09-10T12:00:00Z",
        "ended_at": "2026-09-10T12:00:01Z",
        "mutation_attempted": False,
        "readback_verified": False,
        "outcome": "prepared",
    }
    rendered = caplog.messages[-1]
    assert json.dumps(payload, sort_keys=True) in rendered
    for forbidden in ("credential", "token", "endpoint", "/workspaces", "http://"):
        assert forbidden not in rendered.lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_id", "not-a-uuid"),
        ("mutation_domain_id", "bad domain"),
        ("target_id", "BadTarget"),
        ("operation", "repo stage"),
        ("artifact_sha256", "short"),
        ("size_bytes", -1),
        ("started_at", None),
        ("started_at", 123),
        ("started_at", "not-a-timestamp"),
    ],
)
def test_audit_record_rejects_malformed_or_unbounded_identity_fields(field, value):
    values = {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "mutation_domain_id": "physical-kodi",
        "target_id": "living-room",
        "operation": "repo_stage_snapshot",
        "artifact_sha256": "a" * 64,
        "size_bytes": 1234,
        "started_at": "2026-09-10T12:00:00Z",
        "ended_at": None,
        "mutation_attempted": False,
        "readback_verified": False,
        "outcome": MutationOutcomeStatus.PREPARED,
    }
    values[field] = value

    with pytest.raises(ValueError):
        MutationAuditRecord(**values)


def test_audit_timestamp_accepts_in_progress_and_completed_ranges():
    common = {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "mutation_domain_id": "physical-kodi",
        "target_id": "living-room",
        "operation": "repo_stage_snapshot",
        "artifact_sha256": "a" * 64,
        "size_bytes": 1234,
        "mutation_attempted": False,
        "readback_verified": False,
        "outcome": MutationOutcomeStatus.PREPARED,
    }
    ongoing = MutationAuditRecord(
        **common, started_at="2026-09-10T12:00:00Z", ended_at=None
    )
    completed = MutationAuditRecord(
        **common,
        started_at="2026-09-10T12:00:00Z",
        ended_at="2026-09-10T12:00:01Z",
    )
    assert ongoing.ended_at is None
    assert completed.ended_at == "2026-09-10T12:00:01Z"


def test_audit_timestamp_rejects_end_before_start():
    with pytest.raises(ValueError, match="ended_at must not precede started_at"):
        MutationAuditRecord(
            operation_id="11111111-1111-4111-8111-111111111111",
            mutation_domain_id="physical-kodi",
            target_id="living-room",
            operation="repo_stage_snapshot",
            artifact_sha256="a" * 64,
            size_bytes=1234,
            started_at="2026-09-10T12:00:02Z",
            ended_at="2026-09-10T12:00:01Z",
            mutation_attempted=False,
            readback_verified=False,
            outcome=MutationOutcomeStatus.PREPARED,
        )
