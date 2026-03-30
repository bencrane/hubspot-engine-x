from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CreateOrgRequest(BaseModel):
    id: UUID | None = None
    name: str
    slug: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        min_length=2,
        max_length=50,
    )


class OrgResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime


class AdminCreateUserRequest(BaseModel):
    id: UUID | None = None
    org_id: UUID
    email: str
    name: str | None = None
    role: Literal["org_admin", "company_admin", "company_member"]
    client_id: UUID | None = None


class AdminUserResponse(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    name: str | None
    role: str
    client_id: UUID | None
    is_active: bool
    created_at: datetime
