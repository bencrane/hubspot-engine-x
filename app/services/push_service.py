"""Push service — batch record upserts with field mapping resolution and logging.

All push operations resolve canonical field names to HubSpot property names via
crm_field_mappings at runtime. Results are logged to crm_push_logs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.services.hubspot import HubSpotAPIError, HubSpotClient

_BATCH_SIZE = 100


@dataclass
class PushResult:
    push_log_id: uuid.UUID
    total: int
    succeeded: int
    failed: int
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Field mapping resolution
# ---------------------------------------------------------------------------


async def resolve_field_mappings(
    db: asyncpg.Connection,
    org_id: str,
    client_id: str,
    canonical_object: str,
) -> dict[str, str]:
    """Return {canonical_field: hubspot_property} for a client + object type."""
    rows = await db.fetch(
        """
        SELECT canonical_field, hubspot_property
        FROM crm_field_mappings
        WHERE org_id = $1 AND client_id = $2 AND canonical_object = $3
              AND is_active = TRUE
        """,
        uuid.UUID(org_id),
        uuid.UUID(client_id),
        canonical_object,
    )
    return {row["canonical_field"]: row["hubspot_property"] for row in rows}


def apply_field_mappings(
    records: list[dict[str, Any]],
    mapping: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Transform record property keys from canonical to HubSpot names.

    Returns (mapped_records, unmapped_field_warnings).
    Unmapped fields are dropped from individual records — not rejected entirely.
    """
    mapped: list[dict[str, Any]] = []
    unmapped_fields: set[str] = set()

    for record in records:
        properties = record.get("properties", record)
        mapped_props: dict[str, Any] = {}
        for key, value in properties.items():
            if key in mapping:
                mapped_props[mapping[key]] = value
            else:
                unmapped_fields.add(key)

        if isinstance(record, dict) and "properties" in record:
            mapped_record = {**record, "properties": mapped_props}
        else:
            mapped_record = mapped_props
        mapped.append(mapped_record)

    warnings = [f"Unmapped field skipped: {f}" for f in sorted(unmapped_fields)]
    return mapped, warnings


# ---------------------------------------------------------------------------
# Push logging
# ---------------------------------------------------------------------------


async def log_push(
    db: asyncpg.Connection,
    *,
    org_id: str,
    client_id: str,
    connection_id: str,
    pushed_by: str | None,
    object_type: str,
    total: int,
    succeeded: int,
    failed: int,
    push_status: str,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> uuid.UUID:
    """Insert a record into crm_push_logs and return the log ID."""
    # Look up connection UUID from nango_connection_id
    conn_row = await db.fetchrow(
        """
        SELECT id FROM crm_connections
        WHERE nango_connection_id = $1 AND org_id = $2
        """,
        connection_id,
        uuid.UUID(org_id),
    )
    connection_uuid = conn_row["id"] if conn_row else None

    pushed_by_uuid = uuid.UUID(pushed_by) if pushed_by else None
    payload_json = json.dumps(payload) if payload is not None else None
    result_json = json.dumps(result) if result is not None else None

    row = await db.fetchrow(
        """
        INSERT INTO crm_push_logs
            (org_id, client_id, connection_id, pushed_by, status, object_type,
             records_total, records_succeeded, records_failed,
             payload, result, error_message, started_at, completed_at)
        VALUES ($1, $2, $3, $4, $5::push_status, $6, $7, $8, $9,
                $10::jsonb, $11::jsonb, $12, $13, $14)
        RETURNING id
        """,
        uuid.UUID(org_id),
        uuid.UUID(client_id),
        connection_uuid,
        pushed_by_uuid,
        push_status,
        object_type,
        total,
        succeeded,
        failed,
        payload_json,
        result_json,
        error_message,
        started_at,
        completed_at,
    )
    return uuid.UUID(str(row["id"]))


def _determine_status(succeeded: int, failed: int) -> str:
    if failed == 0:
        return "succeeded"
    if succeeded == 0:
        return "failed"
    return "partial"


# ---------------------------------------------------------------------------
# Batch upsert
# ---------------------------------------------------------------------------


async def batch_upsert(
    hs: HubSpotClient,
    db: asyncpg.Connection,
    *,
    org_id: str,
    client_id: str,
    connection_id: str,
    pushed_by: str | None,
    object_type: str,
    records: list[dict[str, Any]],
    id_property: str | None = None,
) -> PushResult:
    started_at = datetime.now(timezone.utc)
    mapping = await resolve_field_mappings(db, org_id, client_id, object_type)

    mapped_records, warnings = apply_field_mappings(records, mapping)

    total = len(mapped_records)
    succeeded = 0
    failed = 0
    errors: list[dict[str, Any]] = []

    for i in range(0, total, _BATCH_SIZE):
        chunk = mapped_records[i : i + _BATCH_SIZE]
        inputs = []
        for rec in chunk:
            entry: dict[str, Any] = {"properties": rec if not isinstance(rec, dict) or "properties" not in rec else rec["properties"]}
            if id_property:
                entry["idProperty"] = id_property
            inputs.append(entry)

        try:
            data = await hs.batch_upsert(object_type, inputs)
            chunk_results = data.get("results", [])
            succeeded += len(chunk_results)
            chunk_errors = data.get("errors", [])
            failed += len(chunk_errors)
            for err in chunk_errors:
                errors.append({
                    "message": err.get("message", ""),
                    "category": err.get("category", ""),
                    "context": err.get("context", {}),
                })
        except HubSpotAPIError as exc:
            failed += len(chunk)
            errors.append({
                "message": exc.message,
                "category": exc.category,
                "correlation_id": exc.correlation_id,
                "batch_offset": i,
            })

    completed_at = datetime.now(timezone.utc)
    push_status = _determine_status(succeeded, failed)

    log_id = await log_push(
        db,
        org_id=org_id,
        client_id=client_id,
        connection_id=connection_id,
        pushed_by=pushed_by,
        object_type=object_type,
        total=total,
        succeeded=succeeded,
        failed=failed,
        push_status=push_status,
        result={"errors": errors} if errors else None,
        started_at=started_at,
        completed_at=completed_at,
    )

    return PushResult(
        push_log_id=log_id,
        total=total,
        succeeded=succeeded,
        failed=failed,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Batch update
# ---------------------------------------------------------------------------


async def batch_update(
    hs: HubSpotClient,
    db: asyncpg.Connection,
    *,
    org_id: str,
    client_id: str,
    connection_id: str,
    pushed_by: str | None,
    object_type: str,
    updates: list[dict[str, Any]],
) -> PushResult:
    started_at = datetime.now(timezone.utc)

    total = len(updates)
    succeeded = 0
    failed = 0
    errors: list[dict[str, Any]] = []

    for i in range(0, total, _BATCH_SIZE):
        chunk = updates[i : i + _BATCH_SIZE]
        inputs = [{"id": u["id"], "properties": u["properties"]} for u in chunk]

        try:
            data = await hs.batch_update(object_type, inputs)
            chunk_results = data.get("results", [])
            succeeded += len(chunk_results)
            chunk_errors = data.get("errors", [])
            failed += len(chunk_errors)
            for err in chunk_errors:
                errors.append({
                    "message": err.get("message", ""),
                    "category": err.get("category", ""),
                    "context": err.get("context", {}),
                })
        except HubSpotAPIError as exc:
            failed += len(chunk)
            errors.append({
                "message": exc.message,
                "category": exc.category,
                "correlation_id": exc.correlation_id,
                "batch_offset": i,
            })

    completed_at = datetime.now(timezone.utc)
    push_status = _determine_status(succeeded, failed)

    log_id = await log_push(
        db,
        org_id=org_id,
        client_id=client_id,
        connection_id=connection_id,
        pushed_by=pushed_by,
        object_type=object_type,
        total=total,
        succeeded=succeeded,
        failed=failed,
        push_status=push_status,
        result={"errors": errors} if errors else None,
        started_at=started_at,
        completed_at=completed_at,
    )

    return PushResult(
        push_log_id=log_id,
        total=total,
        succeeded=succeeded,
        failed=failed,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Create associations
# ---------------------------------------------------------------------------


async def create_associations(
    hs: HubSpotClient,
    db: asyncpg.Connection,
    *,
    org_id: str,
    client_id: str,
    connection_id: str,
    pushed_by: str | None,
    from_object_type: str,
    to_object_type: str,
    associations: list[dict[str, Any]],
) -> PushResult:
    started_at = datetime.now(timezone.utc)

    total = len(associations)
    succeeded = 0
    failed = 0
    errors: list[dict[str, Any]] = []

    for i in range(0, total, _BATCH_SIZE):
        chunk = associations[i : i + _BATCH_SIZE]
        inputs = []
        for assoc in chunk:
            entry: dict[str, Any] = {
                "from": {"id": assoc["from_id"]},
                "to": {"id": assoc["to_id"]},
            }
            if assoc.get("association_type"):
                entry["types"] = [
                    {"associationCategory": "USER_DEFINED", "associationTypeId": assoc["association_type"]}
                ]
            inputs.append(entry)

        try:
            data = await hs.batch_create_associations(
                from_object_type, to_object_type, inputs
            )
            chunk_results = data.get("results", [])
            succeeded += len(chunk_results)
            chunk_errors = data.get("errors", [])
            failed += len(chunk_errors)
            for err in chunk_errors:
                errors.append({
                    "message": err.get("message", ""),
                    "category": err.get("category", ""),
                    "context": err.get("context", {}),
                })
        except HubSpotAPIError as exc:
            failed += len(chunk)
            errors.append({
                "message": exc.message,
                "category": exc.category,
                "correlation_id": exc.correlation_id,
                "batch_offset": i,
            })

    completed_at = datetime.now(timezone.utc)
    push_status = _determine_status(succeeded, failed)
    log_object = f"{from_object_type}->{to_object_type}"

    log_id = await log_push(
        db,
        org_id=org_id,
        client_id=client_id,
        connection_id=connection_id,
        pushed_by=pushed_by,
        object_type=log_object,
        total=total,
        succeeded=succeeded,
        failed=failed,
        push_status=push_status,
        result={"errors": errors} if errors else None,
        started_at=started_at,
        completed_at=completed_at,
    )

    return PushResult(
        push_log_id=log_id,
        total=total,
        succeeded=succeeded,
        failed=failed,
        errors=errors,
    )
