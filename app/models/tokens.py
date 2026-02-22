from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CreateTokenRequest(BaseModel):
    user_id: UUID
    label: str | None = None
    expires_at: datetime | None = None


class CreateTokenResponse(BaseModel):
    id: UUID
    token: str
    user_id: UUID
    label: str | None
    is_active: bool
    expires_at: datetime | None
    created_at: datetime


class ListTokensRequest(BaseModel):
    user_id: UUID | None = None
    is_active: bool | None = None


class TokenResponse(BaseModel):
    id: UUID
    org_id: UUID
    user_id: UUID
    label: str | None
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ListTokensResponse(BaseModel):
    tokens: list[TokenResponse]


class RevokeTokenRequest(BaseModel):
    token_id: UUID


class RevokeTokenResponse(BaseModel):
    id: UUID
    is_active: bool
