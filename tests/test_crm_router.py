"""Tests for CRM read router (app/routers/crm.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.crm import (
    CrmRecord,
    CrmSearchResponse,
    CrmListResponse,
    CrmGetResponse,
    CrmBatchReadResponse,
)
from app.routers.crm import _to_crm_record, _to_association_records


class TestToCrmRecord:
    def test_basic(self):
        raw = {
            "id": "101",
            "properties": {"email": "test@example.com"},
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-02T00:00:00Z",
        }
        record = _to_crm_record(raw)

        assert record.id == "101"
        assert record.properties["email"] == "test@example.com"
        assert record.created_at is not None

    def test_missing_timestamps(self):
        raw = {"id": "102", "properties": {}}
        record = _to_crm_record(raw)

        assert record.id == "102"
        assert record.created_at is None
        assert record.updated_at is None

    def test_missing_properties(self):
        raw = {"id": "103"}
        record = _to_crm_record(raw)

        assert record.id == "103"
        assert record.properties == {}


class TestToAssociationRecords:
    def test_v4_batch_format(self):
        raw_results = [
            {
                "from": {"id": "1"},
                "to": [
                    {
                        "id": "10",
                        "associationTypes": [
                            {"category": "HUBSPOT_DEFINED", "typeId": 1, "label": None}
                        ],
                    },
                    {
                        "id": "11",
                        "associationTypes": [
                            {"category": "HUBSPOT_DEFINED", "typeId": 2, "label": "Primary"}
                        ],
                    },
                ],
            }
        ]

        records = _to_association_records(raw_results)

        assert len(records) == 2
        assert records[0].from_id == "1"
        assert records[0].to_id == "10"
        assert records[1].to_id == "11"

    def test_empty_results(self):
        assert _to_association_records([]) == []

    def test_no_associations(self):
        raw = [{"from": {"id": "1"}, "to": []}]
        assert _to_association_records(raw) == []
