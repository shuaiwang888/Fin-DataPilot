"""Minimal browser identity bootstrap endpoint."""
from fastapi import APIRouter, Depends, Header, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.security import AuthContext, decode_optional_bearer, issue_anonymous_token, require_user

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post("/anonymous")
@limiter.limit("12/hour")
async def create_anonymous_identity(
    request: Request, authorization: str | None = Header(default=None)
) -> dict[str, object]:
    """Create an identity or renew a still-valid browser identity."""
    existing = decode_optional_bearer(authorization)
    token, context = issue_anonymous_token(existing.user_id if existing else None)
    return {"access_token": token, "token_type": "bearer", "user_id": context.user_id}


@router.get("/me")
async def me(context: AuthContext = Depends(require_user)) -> dict[str, object]:
    return {"user_id": context.user_id, "is_admin": context.is_admin}
