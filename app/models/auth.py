from uuid import UUID

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginUserDetail(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    name: str | None
    role: str
    client_id: UUID | None


class LoginResponse(BaseModel):
    token: str
    user: LoginUserDetail


class MeResponse(BaseModel):
    org_id: str
    user_id: str
    role: str
    permissions: list[str]
    client_id: str | None
    auth_method: str
