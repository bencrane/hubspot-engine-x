from fastapi import APIRouter, Depends

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_auth
from app.models.auth import MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def me(auth: AuthContext = Depends(get_current_auth)) -> MeResponse:
    return MeResponse(
        org_id=auth.org_id,
        user_id=auth.user_id,
        role=auth.role,
        permissions=auth.permissions,
        client_id=auth.client_id,
        auth_method=auth.auth_method,
    )
