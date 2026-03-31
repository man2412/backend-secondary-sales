import time
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.modules.users.repository import UserRepository

security = HTTPBearer(auto_error=False)

# jwks_url -> (monotonic_time, payload)
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _auth_401(detail: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _jwks_urls_for_token(issuer_from_claims: str | None) -> list[str]:
    """Prefer JWT iss JWKS first (e.g. alt.supabase.io) so kid/alg match before project-ref host."""
    urls: list[str] = []
    iss = (issuer_from_claims or "").strip().rstrip("/")
    if iss.startswith("https://") and "/auth/v1" in iss:
        urls.append(f"{iss}/.well-known/jwks.json")
    if settings.SUPABASE_URL.strip():
        default = settings.supabase_jwks_url
        if default not in urls:
            urls.append(default)
    return urls


async def _fetch_jwks_url(url: str, *, force_refresh: bool) -> dict[str, Any]:
    global _jwks_cache
    now = time.monotonic()
    ttl = float(settings.SUPABASE_JWKS_CACHE_SECONDS)
    cached = _jwks_cache.get(url)
    if not force_refresh and cached is not None and now - cached[0] < ttl:
        return cached[1]

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise _auth_401("Could not load signing keys from Supabase") from e

    data = response.json()
    _jwks_cache[url] = (now, data)
    return data


def _pick_jwk(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


def _keys_matching_alg(jwks: dict[str, Any], alg: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in jwks.get("keys", []):
        kid = key.get("kid")
        dedupe = kid if isinstance(kid, str) else str(id(key))
        if dedupe in seen:
            continue
        if key.get("alg") == alg:
            out.append(key)
            seen.add(dedupe)
            continue
        if alg == "RS256" and key.get("kty") == "RSA":
            out.append(key)
            seen.add(dedupe)
        elif alg == "ES256" and key.get("kty") == "EC":
            out.append(key)
            seen.add(dedupe)
    return out


def _candidate_jwk_entries(jwks: dict[str, Any], alg: str, kid: str | None) -> list[dict[str, Any]]:
    """Prefer kid match, then any key usable for alg (handles stale/missing kid)."""
    ordered: list[dict[str, Any]] = []
    seen_kid: set[str] = set()

    if kid:
        picked = _pick_jwk(jwks, kid)
        if picked is not None:
            ordered.append(picked)
            if isinstance(picked.get("kid"), str):
                seen_kid.add(picked["kid"])

    for key in _keys_matching_alg(jwks, alg):
        k = key.get("kid")
        if isinstance(k, str) and k in seen_kid:
            continue
        ordered.append(key)
        if isinstance(k, str):
            seen_kid.add(k)

    return ordered


def _jwks_algorithm_summary(jwks: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in jwks.get("keys", []):
        parts.append(f"{key.get('alg') or '?'}/{key.get('kty') or '?'}")
    return ", ".join(parts) if parts else "none"


async def _decode_asymmetric_jwks(token: str, header: dict[str, Any], alg: str) -> dict[str, Any]:
    audience = "authenticated"
    try:
        claims = jwt.get_unverified_claims(token)
    except JWTError as e:
        raise _auth_401() from e

    iss = claims.get("iss") if isinstance(claims, dict) else None
    iss_str = iss if isinstance(iss, str) else None

    urls = _jwks_urls_for_token(iss_str)
    if not urls:
        raise _auth_401("SUPABASE_URL is not set; cannot verify asymmetric (JWKS) tokens")

    kid = header.get("kid")
    if kid is not None and not isinstance(kid, str):
        kid = str(kid)

    last_decode_error: JWTError | None = None
    summary_for_hint = ""

    for url in urls:
        for force_refresh in (False, True):
            jwks = await _fetch_jwks_url(url, force_refresh=force_refresh)
            summary_for_hint = _jwks_algorithm_summary(jwks)
            candidates = _candidate_jwk_entries(jwks, alg, kid)

            for key_data in candidates:
                try:
                    public_key = jwk.construct(key_data)
                    return jwt.decode(
                        token,
                        public_key,
                        algorithms=[alg],
                        audience=audience,
                    )
                except JWTError as e:
                    last_decode_error = e
                    continue

    if last_decode_error is not None and type(last_decode_error).__name__ == "ExpiredSignatureError":
        raise _auth_401(
            "Access token has expired. Sign in again and send a fresh access_token in Authorization."
        ) from last_decode_error

    if alg == "RS256" and "RSA" not in summary_for_hint and "RS256" not in summary_for_hint:
        raise _auth_401(
            "JWT is RS256 but this project's JWKS has no RSA key (published: "
            f"{summary_for_hint}). Use session.access_token from supabase.auth.getSession(), "
            "not the OpenID id_token."
        ) from last_decode_error

    if kid:
        raise _auth_401(
            f"Signing key not found for this token (kid={kid!r}). "
            f"JWKS key types: {summary_for_hint}. Check SUPABASE_URL matches the project that minted the JWT."
        ) from last_decode_error

    raise _auth_401("Signing key not found for this token") from last_decode_error


async def decode_supabase_jwt(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise _auth_401() from e

    alg = header.get("alg")

    if alg == "HS256":
        secret = (settings.SUPABASE_JWT_SECRET or "").strip()
        if not secret:
            raise _auth_401("SUPABASE_JWT_SECRET is not set; cannot verify HS256 tokens")
        try:
            return jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except JWTError as e:
            if type(e).__name__ == "ExpiredSignatureError":
                raise _auth_401(
                    "Access token has expired. Sign in again and send a fresh access_token in Authorization."
                ) from e
            raise _auth_401() from e

    if alg in ("ES256", "RS256"):
        return await _decode_asymmetric_jwks(token, header, alg)

    raise _auth_401(f"Unsupported token algorithm: {alg}")


async def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return await decode_supabase_jwt(credentials.credentials)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = await decode_supabase_jwt(credentials.credentials)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        supabase_id = UUID(sub)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    repo = UserRepository()
    user = await repo.get_by_supabase_id(db, supabase_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive. Call /auth/sync-user after provisioning.",
        )
    return user


def require_roles(*roles: UserRole):
    async def role_checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return role_checker
