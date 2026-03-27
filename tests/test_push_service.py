"""Tests for push service (app/services/push_service.py)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.services.push_service import (
    apply_field_mappings,
    resolve_field_mappings,
    _determine_status,
    _execute_batched_push,
    _validate_payload_size,
    _check_idempotency,
    batch_upsert,
    batch_update,
    create_associations,
)
from app.services.hubspot import HubSpotAPIError


class TestApplyFieldMappings:
    def test_basic_mapping(self):
        records = [
            {"properties": {"email_address": "a@b.com", "first_name": "Alice"}},
        ]
        mapping = {
            "email_address": "email",
            "first_name": "firstname",
        }

        mapped, warnings = apply_field_mappings(records, mapping)

        assert len(mapped) == 1
        assert mapped[0]["properties"]["email"] == "a@b.com"
        assert mapped[0]["properties"]["firstname"] == "Alice"
        assert warnings == []

    def test_unmapped_fields_skipped(self):
        records = [
            {"properties": {"email_address": "a@b.com", "unknown_field": "val"}},
        ]
        mapping = {"email_address": "email"}

        mapped, warnings = apply_field_mappings(records, mapping)

        assert "email" in mapped[0]["properties"]
        assert "unknown_field" not in mapped[0]["properties"]
        assert len(warnings) == 1
        assert "unknown_field" in warnings[0]

    def test_flat_record_format(self):
        records = [
            {"email_address": "a@b.com", "first_name": "Alice"},
        ]
        mapping = {"email_address": "email", "first_name": "firstname"}

        mapped, warnings = apply_field_mappings(records, mapping)

        assert mapped[0]["email"] == "a@b.com"
        assert mapped[0]["firstname"] == "Alice"

    def test_empty_mapping(self):
        records = [{"properties": {"email": "a@b.com"}}]
        mapping: dict[str, str] = {}

        mapped, warnings = apply_field_mappings(records, mapping)

        assert mapped[0]["properties"] == {}
        assert len(warnings) == 1

    def test_empty_records(self):
        mapped, warnings = apply_field_mappings([], {"a": "b"})
        assert mapped == []
        assert warnings == []


class TestDetermineStatus:
    def test_all_succeeded(self):
        assert _determine_status(10, 0) == "succeeded"

    def test_all_failed(self):
        assert _determine_status(0, 10) == "failed"

    def test_partial(self):
        assert _determine_status(7, 3) == "partial"

    def test_zero_zero(self):
        assert _determine_status(0, 0) == "succeeded"


class TestResolveFieldMappings:
    @pytest.mark.asyncio
    async def test_returns_mapping_dict(self):
        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(
            return_value=[
                {"canonical_field": "email_address", "hubspot_property": "email"},
                {"canonical_field": "first_name", "hubspot_property": "firstname"},
            ]
        )

        result = await resolve_field_mappings(
            mock_db,
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            "contact",
        )

        assert result == {
            "email_address": "email",
            "first_name": "firstname",
        }

    @pytest.mark.asyncio
    async def test_empty_mappings(self):
        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(return_value=[])

        result = await resolve_field_mappings(
            mock_db,
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            "contact",
        )

        assert result == {}


class TestBatchUpsert:
    @pytest.mark.asyncio
    async def test_chunks_and_logs(self):
        mock_hs = AsyncMock()
        mock_hs.batch_upsert = AsyncMock(
            return_value={
                "results": [{"id": "1"}],
                "errors": [],
            }
        )

        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(return_value=[
            {"canonical_field": "email_address", "hubspot_property": "email"},
        ])
        mock_db.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})

        records = [{"properties": {"email_address": f"user{i}@test.com"}} for i in range(150)]

        result = await batch_upsert(
            mock_hs,
            mock_db,
            org_id="00000000-0000-0000-0000-000000000001",
            client_id="00000000-0000-0000-0000-000000000002",
            connection_id="00000000-0000-0000-0000-000000000002",
            pushed_by="00000000-0000-0000-0000-000000000010",
            object_type="contacts",
            records=records,
        )

        # Should have been called twice (100 + 50)
        assert mock_hs.batch_upsert.call_count == 2
        assert result.total == 150
        assert result.succeeded == 2  # 1 per batch call
        assert result.push_log_id is not None

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        call_count = 0

        async def mock_batch_upsert(object_type, inputs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"results": [{"id": "1"}], "errors": []}
            raise HubSpotAPIError(429, "RATE_LIMIT", "Too many requests", "corr-123")

        mock_hs = AsyncMock()
        mock_hs.batch_upsert = mock_batch_upsert

        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(return_value=[])
        mock_db.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})

        records = [{"properties": {"raw_field": f"val{i}"}} for i in range(150)]

        result = await batch_upsert(
            mock_hs,
            mock_db,
            org_id="00000000-0000-0000-0000-000000000001",
            client_id="00000000-0000-0000-0000-000000000002",
            connection_id="00000000-0000-0000-0000-000000000002",
            pushed_by=None,
            object_type="contacts",
            records=records,
        )

        assert result.succeeded == 1
        assert result.failed == 50
        assert len(result.errors) == 1
        assert result.errors[0]["correlation_id"] == "corr-123"


# ---------------------------------------------------------------------------
# Shared batch helper tests (item 16)
# ---------------------------------------------------------------------------


class TestExecuteBatchedPush:
    @pytest.mark.asyncio
    async def test_single_batch(self):
        async def mock_call(inputs):
            return {"results": [{"id": str(i)} for i in range(len(inputs))], "errors": []}

        succeeded, failed, errors = await _execute_batched_push(mock_call, [{"x": 1}] * 50)
        assert succeeded == 50
        assert failed == 0
        assert errors == []

    @pytest.mark.asyncio
    async def test_multiple_batches(self):
        call_count = 0

        async def mock_call(inputs):
            nonlocal call_count
            call_count += 1
            return {"results": [{"id": "x"}] * len(inputs), "errors": []}

        succeeded, failed, errors = await _execute_batched_push(
            mock_call, [{"x": 1}] * 250, batch_size=100
        )
        assert succeeded == 250
        assert failed == 0
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        call_count = 0

        async def mock_call(inputs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise HubSpotAPIError(500, "SERVER_ERROR", "Internal error")
            return {"results": [{"id": "x"}] * len(inputs), "errors": []}

        succeeded, failed, errors = await _execute_batched_push(
            mock_call, [{"x": 1}] * 200, batch_size=100
        )
        assert succeeded == 100
        assert failed == 100
        assert len(errors) == 1
        assert errors[0]["category"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_partial_batch_errors(self):
        async def mock_call(inputs):
            return {
                "results": [{"id": "1"}],
                "errors": [{"message": "bad record", "category": "VALIDATION_ERROR", "context": {}}],
            }

        succeeded, failed, errors = await _execute_batched_push(mock_call, [{"x": 1}] * 2)
        assert succeeded == 1
        assert failed == 1
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# Association input shaping tests (item 16)
# ---------------------------------------------------------------------------


class TestCreateAssociations:
    @pytest.mark.asyncio
    async def test_association_input_shaping(self):
        mock_hs = AsyncMock()
        mock_hs.batch_create_associations = AsyncMock(
            return_value={"results": [{"id": "1"}], "errors": []}
        )

        mock_db = AsyncMock()
        mock_db.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})

        associations = [
            {"from_id": "100", "to_id": "200", "association_type": "42"},
            {"from_id": "101", "to_id": "201", "association_type": None},
        ]

        result = await create_associations(
            mock_hs,
            mock_db,
            org_id="00000000-0000-0000-0000-000000000001",
            client_id="00000000-0000-0000-0000-000000000002",
            connection_id="conn-1",
            pushed_by=None,
            from_object_type="contacts",
            to_object_type="companies",
            associations=associations,
        )

        # Verify the call was made
        assert mock_hs.batch_create_associations.call_count == 1
        call_args = mock_hs.batch_create_associations.call_args
        inputs = call_args[0][2]  # third positional arg

        # First association should have types
        assert inputs[0]["from"]["id"] == "100"
        assert inputs[0]["to"]["id"] == "200"
        assert "types" in inputs[0]
        assert inputs[0]["types"][0]["associationTypeId"] == "42"

        # Second association should NOT have types (None)
        assert inputs[1]["from"]["id"] == "101"
        assert "types" not in inputs[1]

    @pytest.mark.asyncio
    async def test_association_batching(self):
        """Associations > 100 should be chunked."""
        mock_hs = AsyncMock()
        mock_hs.batch_create_associations = AsyncMock(
            return_value={"results": [{"id": "x"}], "errors": []}
        )

        mock_db = AsyncMock()
        mock_db.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})

        associations = [
            {"from_id": str(i), "to_id": str(i + 1000), "association_type": None}
            for i in range(150)
        ]

        result = await create_associations(
            mock_hs,
            mock_db,
            org_id="00000000-0000-0000-0000-000000000001",
            client_id="00000000-0000-0000-0000-000000000002",
            connection_id="conn-1",
            pushed_by=None,
            from_object_type="contacts",
            to_object_type="companies",
            associations=associations,
        )

        assert mock_hs.batch_create_associations.call_count == 2
        assert result.total == 150


# ---------------------------------------------------------------------------
# Payload validation test (item 10)
# ---------------------------------------------------------------------------


class TestPayloadValidation:
    def test_small_payload_passes(self):
        _validate_payload_size([{"properties": {"email": "a@b.com"}}])

    def test_oversized_payload_raises(self):
        big_inputs = [{"properties": {"data": "x" * 100_000}} for _ in range(50)]
        with pytest.raises(Exception) as exc_info:
            _validate_payload_size(big_inputs)
        assert "3MB" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# Idempotency tests (item 11)
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_idempotency_cache_hit(self):
        mock_db = AsyncMock()
        log_id = uuid.uuid4()
        mock_db.fetchrow = AsyncMock(return_value={
            "id": log_id,
            "records_total": 10,
            "records_succeeded": 10,
            "records_failed": 0,
            "result": None,
            "status": "succeeded",
        })

        result = await _check_idempotency(
            mock_db,
            "00000000-0000-0000-0000-000000000001",
            "test-key-1",
        )

        assert result is not None
        assert result.push_log_id == log_id
        assert result.total == 10
        assert "Idempotent replay" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_idempotency_cache_miss(self):
        mock_db = AsyncMock()
        mock_db.fetchrow = AsyncMock(return_value=None)

        result = await _check_idempotency(
            mock_db,
            "00000000-0000-0000-0000-000000000001",
            "new-key",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_idempotency_failed_allows_retry(self):
        """Failed push with same key should allow re-execution."""
        mock_db = AsyncMock()
        mock_db.fetchrow = AsyncMock(return_value={
            "id": uuid.uuid4(),
            "records_total": 10,
            "records_succeeded": 0,
            "records_failed": 10,
            "result": None,
            "status": "failed",
        })

        result = await _check_idempotency(
            mock_db,
            "00000000-0000-0000-0000-000000000001",
            "retry-key",
        )

        assert result is None  # allows retry


# ---------------------------------------------------------------------------
# Batch update with field mapping (item 7)
# ---------------------------------------------------------------------------


class TestBatchUpdateFieldMapping:
    @pytest.mark.asyncio
    async def test_batch_update_resolves_field_mappings(self):
        mock_hs = AsyncMock()
        mock_hs.batch_update = AsyncMock(
            return_value={"results": [{"id": "1"}], "errors": []}
        )

        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(return_value=[
            {"canonical_field": "email_address", "hubspot_property": "email"},
        ])
        mock_db.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})

        updates = [
            {"id": "rec-1", "properties": {"email_address": "new@example.com"}},
        ]

        result = await batch_update(
            mock_hs,
            mock_db,
            org_id="00000000-0000-0000-0000-000000000001",
            client_id="00000000-0000-0000-0000-000000000002",
            connection_id="conn-1",
            pushed_by=None,
            object_type="contacts",
            updates=updates,
        )

        # Verify mapped property name was used
        call_args = mock_hs.batch_update.call_args
        inputs = call_args[0][1]  # second positional arg
        assert inputs[0]["properties"]["email"] == "new@example.com"
        assert "email_address" not in inputs[0]["properties"]
