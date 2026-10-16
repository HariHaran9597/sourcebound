"""OIDC token verification and organization identity resolution."""

from dataclasses import dataclass
from functools import lru_cache
import json
from urllib.request import urlopen
from uuid import UUID

import jwt
from fastapi import Header, HTTPException, status

from sourcebound_api.config import get_settings


@dataclass(frozen=True)
class Principal:
    subject: str
    organization_id: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, timeout=5, cache_jwk_set=True, lifespan=300)


@lru_cache(maxsize=8)
def _discovered_jwks_url(issuer: str) -> str:
    discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    with urlopen(discovery_url, timeout=5) as response:
        metadata = json.load(response)
    jwks_url = metadata.get("jwks_uri")
    if not isinstance(jwks_url, str):
        raise ValueError("OIDC discovery metadata has no jwks_uri")
    return jwks_url


def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    settings = get_settings()
    if settings.auth_mode == "disabled":
        return Principal(subject="local-development", organization_id=settings.organization_id, groups=(), roles=("sourcebound_admin",))
    if settings.auth_mode != "oidc" or not settings.oidc_issuer or not settings.oidc_audience:
        raise HTTPException(status_code=503, detail="OIDC authentication is enabled but is not configured.")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer access token required.")
    token = authorization[7:].strip()
    try:
        jwks_url = _discovered_jwks_url(settings.oidc_issuer)
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            options={"require": ["exp", "iat", "sub"]},
        )
    except Exception as exc:
        # JWKS/network errors must fail closed like invalid signatures.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token.") from exc
    organization_id = claims.get(settings.oidc_organization_claim)
    if not isinstance(organization_id, str) or not organization_id.strip():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access token has no organization assignment.")
    try:
        organization_id = str(UUID(organization_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access token organization assignment must be a UUID.") from exc
    groups = claims.get("groups", [])
    if (
        not isinstance(groups, list)
        or len(groups) > 100
        or any(
            not isinstance(group, str)
            or not group.strip()
            or group != group.strip()
            or len(group) > 255
            for group in groups
        )
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access token has invalid group claims.")
    roles = claims.get("roles", [])
    if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
        roles = []
    return Principal(subject=claims["sub"], organization_id=organization_id, groups=tuple(groups), roles=tuple(roles))
