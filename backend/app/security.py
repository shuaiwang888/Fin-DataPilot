"""Authentication and authorization helpers for the HTTP API.

The public web app uses a server-signed anonymous bearer identity.  It is a
capability token, not a replacement for an enterprise IdP, but it prevents
different browser visitors from sharing the old ``default`` user namespace.
An OIDC/JWT gateway can later replace ``issue_anonymous_token`` without
changing the API routes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    is_admin: bool = False


def _secret() -> bytes:
    return get_settings().effective_auth_secret.encode("utf-8")


def issue_anonymous_token(user_id: str | None = None) -> tuple[str, AuthContext]:
    """Create an opaque, signed bearer token for one browser profile."""
    settings = get_settings()
    user_id = user_id or f"anon_{secrets.token_urlsafe(18)}"
    if not user_id.startswith("anon_"):
        raise ValueError("Anonymous user id required")
    expires_at = int(time.time()) + settings.auth_token_ttl_seconds
    payload = f"v1.{user_id}.{expires_at}"
    signature = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    token = f"{payload}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"
    return token, AuthContext(user_id=user_id)


def _decode_token(token: str) -> AuthContext:
    try:
        version, user_id, expires_raw, signature = token.split(".", 3)
        expires_at = int(expires_raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token") from exc
    if version != "v1" or not user_id.startswith("anon_") or expires_at < int(time.time()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Expired or invalid bearer token")
    payload = f"{version}.{user_id}.{expires_at}".encode()
    expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
    try:
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token") from exc
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return AuthContext(user_id=user_id)


async def require_user(authorization: str | None = Header(default=None)) -> AuthContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
    return _decode_token(authorization.removeprefix("Bearer ").strip())


def decode_optional_bearer(authorization: str | None) -> AuthContext | None:
    """Decode a valid bearer when present; invalid/expired values renew as new."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return _decode_token(authorization.removeprefix("Bearer ").strip())
    except HTTPException:
        return None


async def require_admin(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> AuthContext:
    """Guard mutable operator endpoints with a distinct server-side key."""
    configured_key = get_settings().operator_api_key
    if not configured_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Admin API is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, configured_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator credentials required")
    # Read the request so rate-limit decorators can retain a normal request
    # argument and so this dependency has the same shape as other guards.
    _ = request
    return AuthContext(user_id="admin", is_admin=True)
