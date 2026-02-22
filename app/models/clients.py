from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CreateClientRequest(BaseModel):
    name: str
    domain: str | None = None


class GetClientRequest(BaseModel):
    client_id: UUID


class ListClientsRequest(BaseModel):
    is_active: bool | None = None


class ClientResponse(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    domain: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ListClientsResponse(BaseModel):
    clients: list[ClientResponse]
