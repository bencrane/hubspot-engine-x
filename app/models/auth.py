from pydantic import BaseModel


class MeResponse(BaseModel):
    org_id: str
    user_id: str
    role: str
    permissions: list[str]
    client_id: str | None
    auth_method: str
