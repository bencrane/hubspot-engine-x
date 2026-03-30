from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class CreateUserRequest(BaseModel):
    email: str
    name: str | None = None
    role: Literal["org_admin", "company_admin", "company_member"]
    client_id: UUID | None = None


class ListUsersRequest(BaseModel):
    client_id: UUID | None = None
    role: Literal["org_admin", "company_admin", "company_member"] | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    name: str | None
    role: str
    client_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ListUsersResponse(BaseModel):
    users: list[UserResponse]
