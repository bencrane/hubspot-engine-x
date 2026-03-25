"""Tests for push service (app/services/push_service.py)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.services.push_service import (
    apply_field_mappings,
    resolve_field_mappings,
    _determine_status,
    batch_upsert,
)


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
            from app.services.hubspot import HubSpotAPIError
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
