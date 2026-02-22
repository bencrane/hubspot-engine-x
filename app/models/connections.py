from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CreateConnectionRequest(BaseModel):
    client_id: UUID


class ConnectionSessionResponse(BaseModel):
    session_token: str
    connect_link: str
    expires_at: str


class CallbackConnectionRequest(BaseModel):
    client_id: UUID


class ConnectionResponse(BaseModel):
    id: UUID
    org_id: UUID
    client_id: UUID
    nango_connection_id: str | None
    status: str
    hub_domain: str | None
    hubspot_portal_id: str | None
    scopes: str | None
    last_used_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ListConnectionsRequest(BaseModel):
    client_id: UUID | None = None
    status: str | None = None


class ListConnectionsResponse(BaseModel):
    connections: list[ConnectionResponse]


class GetConnectionRequest(BaseModel):
    client_id: UUID


class RefreshConnectionRequest(BaseModel):
    client_id: UUID


class RefreshConnectionResponse(BaseModel):
    status: str
    client_id: UUID


class RevokeConnectionRequest(BaseModel):
    client_id: UUID
