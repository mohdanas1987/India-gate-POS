"""
Auth primitives — replaces the legacy auth.js / loggedIn.js pair.

Fixes applied vs. the audited legacy code (Phase 0 findings):
  1. JWT secret comes from Settings (env-backed), never a literal string
     baked into source.
  2. The authenticated principal is derived ONLY from the verified token
     claims. Nothing under this module ever reads an identity from
     request body/query/header fields the caller controls (the legacy bug:
     `/auth/getuser` trusted `req.body.id` instead of the token subject).
  3. Tokens carry a role and a token type (access/refresh) so refresh
     tokens can't be replayed as access tokens.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import bcrypt
import jwt

from app.core.config import get_settings

# NOTE: using the `bcrypt` package directly rather than passlib.
# passlib 1.7.4's bcrypt backend-detection probe is broken against
# modern `bcrypt` releases (it raises on its own self-test with a
# 'password cannot be longer than 72 bytes' error, unrelated to any real
# input) — confirmed by this project's own test suite failing on it.
# Calling bcrypt directly avoids that broken compatibility shim entirely.
_BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    truncated = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(truncated, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    truncated = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(truncated, hashed.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class Principal:
    """The only source of truth for 'who is making this request'.

    CTO audit finding (commit c4bfb82): every route that needed a tenant id
    was hardcoding `tenant_id=1` instead of deriving it from the
    authenticated caller — a real multi-tenant-isolation bug, not just a
    style issue (e.g. it would silently attribute a Store Manager's own
    audit-log entries and orders to a hardcoded tenant that may not even be
    theirs). Principal now carries tenant_id so every route has a single,
    correct source for it — no route should ever write a literal `1`.
    """
    user_id: int
    tenant_id: int
    role: str
    store_id: int | None


class InvalidTokenError(Exception):
    pass


def create_token(user_id: int, tenant_id: int, role: str, store_id: int | None, token_type: str = "access") -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    ttl = (
        dt.timedelta(minutes=settings.jwt_access_token_minutes)
        if token_type == "access"
        else dt.timedelta(days=settings.jwt_refresh_token_days)
    )
    payload = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "role": role,
        "store_id": store_id,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str = "access") -> Principal:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if payload.get("type") != expected_type:
        raise InvalidTokenError(f"expected token type {expected_type!r}, got {payload.get('type')!r}")

    if "tenant_id" not in payload:
        # Tokens issued before this fix don't carry tenant_id — reject them
        # explicitly rather than silently defaulting to tenant 1 again.
        raise InvalidTokenError("token missing tenant_id claim — re-authenticate")

    return Principal(
        user_id=int(payload["sub"]),
        tenant_id=int(payload["tenant_id"]),
        role=payload["role"],
        store_id=payload.get("store_id"),
    )
