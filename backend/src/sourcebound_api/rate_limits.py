"""Database-backed fixed-window rate limits shared across API workers."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, Request, Response
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from sourcebound_api.auth import Principal
from sourcebound_api.config import get_settings
from sourcebound_api.models import RateLimitBucket

WINDOW_SECONDS = 60
RETENTION_SECONDS = 2 * 60 * 60


def enforce_rate_limit(
    session: Session,
    request: Request,
    response: Response,
    principal: Principal,
    *,
    policy: str,
    limit: int,
    scope: Literal["subject", "organization"] = "subject",
) -> None:
    """Consume a shared or subject request bucket and return HTTP 429 when full."""
    now_epoch = int(time.time())
    window_epoch = now_epoch // WINDOW_SECONDS
    identity = "__organization__" if scope == "organization" else principal.subject
    if scope == "subject" and principal.subject == "local-development":
        identity = request.client.host if request.client else "unknown-client"
    key_material = "\x1f".join((principal.organization_id, scope, identity, policy, str(window_epoch)))
    hash_key = get_settings().rate_limit_hmac_key.get_secret_value().encode("utf-8")
    bucket_key = hmac.new(hash_key, key_material.encode("utf-8"), hashlib.sha256).hexdigest()
    resets_at = (window_epoch + 1) * WINDOW_SECONDS
    expires_at = datetime.fromtimestamp(resets_at + RETENTION_SECONDS, UTC)
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgres_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:
        raise HTTPException(status_code=503, detail="Rate limiting is unavailable for this database backend.")

    statement = insert(RateLimitBucket).values(
        bucket_key=bucket_key,
        request_count=1,
        expires_at=expires_at,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[RateLimitBucket.bucket_key],
        set_={"request_count": RateLimitBucket.request_count + 1},
    ).returning(RateLimitBucket.request_count)
    try:
        request_count = session.execute(statement).scalar_one()
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="Rate limiting is temporarily unavailable.") from exc

    reset_after = max(0, resets_at - now_epoch)
    remaining = max(0, limit - request_count)
    if request_count > limit:
        retry_after = max(1, reset_after)
        raise HTTPException(
            status_code=429,
            detail="Request rate limit exceeded. Retry after the current window resets.",
            headers={
                "Retry-After": str(retry_after),
                "RateLimit-Limit": str(limit),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(reset_after),
                "RateLimit-Scope": scope,
            },
        )
    previous_limit = int(response.headers.get("RateLimit-Limit", "0"))
    previous_remaining = int(response.headers.get("RateLimit-Remaining", "0"))
    if previous_limit and previous_remaining / previous_limit <= remaining / limit:
        return
    response.headers["RateLimit-Limit"] = str(limit)
    response.headers["RateLimit-Remaining"] = str(remaining)
    response.headers["RateLimit-Reset"] = str(reset_after)
    response.headers["RateLimit-Scope"] = scope
