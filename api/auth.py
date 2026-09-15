"""Authentication endpoints and JWT boundary for the Neu.Tail demo API.

The bundled customer data contains email addresses, but intentionally contains
no password hashes. Login therefore uses one shared password supplied through
``NEUTAIL_DEMO_PASSWORD``. This is suitable only for the local demo; a real
deployment must delegate credential verification and token issuance to an
identity provider.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Annotated, Any, Literal, Optional
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import func, select

from models.entities import Customer
from tools.runtime import get_runtime


JWT_SECRET_ENV = "NEUTAIL_JWT_SECRET"
DEMO_PASSWORD_ENV = "NEUTAIL_DEMO_PASSWORD"
ACCESS_TOKEN_TTL_ENV = "NEUTAIL_ACCESS_TOKEN_TTL_SECONDS"
JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 3600
MAX_ACCESS_TOKEN_TTL_SECONDS = 86_400

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Neu.Tail JWT access token",
    bearerFormat="JWT",
)
_revoked_tokens: dict[str, float] = {}
_revocation_lock = RLock()


class APIModel(BaseModel):
    """Strict request/response behavior for the authentication boundary."""

    model_config = ConfigDict(extra="forbid")


class LoginRequest(APIModel):
    """Credentials accepted by ``POST /api/v1/auth/login``."""

    email: str = Field(min_length=3, json_schema_extra={"format": "email"})
    password: SecretStr = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not _EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("email must be a valid email address")
        return normalized


class AuthUser(APIModel):
    """Identity exposed to the UI after authentication."""

    customer_id: str
    display_name: str
    email: Optional[str] = Field(
        default=None,
        json_schema_extra={"format": "email"},
    )
    role: Literal["customer", "stylist", "admin"]


class LoginResponse(APIModel):
    """Bearer token and customer identity returned after login."""

    access_token: str
    token_type: Literal["bearer"]
    expires_in: int = Field(gt=0)
    user: AuthUser


class ErrorResponse(APIModel):
    """Error envelope defined by the UI/backend OpenAPI contract."""

    error_code: str
    message: str
    trace_id: Optional[str] = None
    details: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Validated token state retained for authorization and logout."""

    customer_id: str
    token: str
    expires_at: float


def _service_unavailable(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


def _jwt_secret() -> str:
    secret = os.getenv(JWT_SECRET_ENV)
    if not secret or len(secret.encode("utf-8")) < 32:
        raise _service_unavailable("JWT authentication is not securely configured")
    return secret


def _access_token_ttl_seconds() -> int:
    configured = os.getenv(
        ACCESS_TOKEN_TTL_ENV,
        str(DEFAULT_ACCESS_TOKEN_TTL_SECONDS),
    )
    try:
        ttl = int(configured)
    except ValueError as exc:
        raise _service_unavailable(
            f"{ACCESS_TOKEN_TTL_ENV} must be an integer"
        ) from exc
    if not 0 < ttl <= MAX_ACCESS_TOKEN_TTL_SECONDS:
        raise _service_unavailable(
            f"{ACCESS_TOKEN_TTL_ENV} must be between 1 and "
            f"{MAX_ACCESS_TOKEN_TTL_SECONDS}"
        )
    return ttl


def _display_name(customer: Customer) -> str:
    name = " ".join(
        part.strip()
        for part in (customer.first_name, customer.last_name)
        if part and part.strip()
    )
    return name or customer.customer_id


def _to_auth_user(customer: Customer) -> AuthUser:
    return AuthUser(
        customer_id=customer.customer_id,
        display_name=_display_name(customer),
        email=customer.email,
        role="customer",
    )


def _find_user_by_email(email: str) -> Optional[AuthUser]:
    statement = (
        select(Customer)
        .where(func.lower(Customer.email) == email)
        .limit(1)
    )
    with get_runtime().session() as session:
        customer = session.scalars(statement).first()
        return _to_auth_user(customer) if customer is not None else None


def _find_user_by_customer_id(customer_id: str) -> Optional[AuthUser]:
    with get_runtime().session() as session:
        customer = session.get(Customer, customer_id)
        return _to_auth_user(customer) if customer is not None else None


def authenticate_customer(payload: LoginRequest) -> Optional[AuthUser]:
    """Verify demo credentials without revealing which field was incorrect."""

    configured_password = os.getenv(DEMO_PASSWORD_ENV)
    if not configured_password:
        raise _service_unavailable("Demo login is not securely configured")

    user = _find_user_by_email(payload.email)
    password_matches = hmac.compare_digest(
        payload.password.get_secret_value().encode("utf-8"),
        configured_password.encode("utf-8"),
    )
    if user is None or not password_matches:
        return None
    return user


def create_access_token(customer_id: str) -> tuple[str, int]:
    """Issue a short-lived access token for an authenticated customer."""

    normalized_id = customer_id.strip()
    if not normalized_id:
        raise ValueError("customer_id must be a non-empty string")

    ttl = _access_token_ttl_seconds()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": normalized_id,
            "iat": now,
            "exp": now + timedelta(seconds=ttl),
            "jti": uuid4().hex,
            "role": "customer",
        },
        _jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )
    return token, ttl


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_revoked(token: str) -> bool:
    now = datetime.now(timezone.utc).timestamp()
    fingerprint = _token_fingerprint(token)
    with _revocation_lock:
        expired = [
            item
            for item, expiration in _revoked_tokens.items()
            if expiration <= now
        ]
        for item in expired:
            del _revoked_tokens[item]
        return fingerprint in _revoked_tokens


def revoke_access_token(token: str, expires_at: float) -> None:
    """Deny a token until it would naturally expire."""

    with _revocation_lock:
        _revoked_tokens[_token_fingerprint(token)] = expires_at


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_authenticated_identity(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(_bearer)
    ],
) -> AuthenticatedIdentity:
    """Validate a signed, unexpired, non-revoked Bearer access token."""

    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise _unauthorized("A Bearer access token is required")

    try:
        claims = jwt.decode(
            credentials.credentials,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        expires_at = float(claims["exp"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise _unauthorized("The access token is invalid or expired") from exc

    customer_id = claims.get("sub")
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise _unauthorized("The access token has no valid customer subject")

    if _is_revoked(credentials.credentials):
        raise _unauthorized("The access token has been revoked")

    return AuthenticatedIdentity(
        customer_id=customer_id.strip(),
        token=credentials.credentials,
        expires_at=expires_at,
    )


async def get_authenticated_customer_id(
    identity: Annotated[
        AuthenticatedIdentity,
        Depends(get_authenticated_identity),
    ],
) -> str:
    """Return the customer subject from a validated access token."""

    return identity.customer_id


router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=LoginResponse,
    response_description="Login successful",
    summary="Login",
    operation_id="login",
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid credentials",
            "model": ErrorResponse,
        }
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
) -> LoginResponse | JSONResponse:
    user = authenticate_customer(payload)
    if user is None:
        error = ErrorResponse(
            error_code="INVALID_CREDENTIALS",
            message="The email or password is incorrect",
            trace_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=error.model_dump(mode="json"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token, expires_in = create_access_token(user.customer_id)
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        user=user,
    )


@router.get(
    "/me",
    response_model=AuthUser,
    response_description="Authenticated identity",
    summary="Get current authenticated identity",
    operation_id="getCurrentUser",
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Unauthorized",
        }
    },
)
async def get_current_user(
    identity: Annotated[
        AuthenticatedIdentity,
        Depends(get_authenticated_identity),
    ],
) -> AuthUser:
    user = _find_user_by_customer_id(identity.customer_id)
    if user is None:
        raise _unauthorized("The authenticated customer no longer exists")
    return user


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_description="Logged out",
    summary="Logout",
    operation_id="logout",
)
async def logout(
    identity: Annotated[
        AuthenticatedIdentity,
        Depends(get_authenticated_identity),
    ],
) -> Response:
    revoke_access_token(identity.token, identity.expires_at)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = [
    "ACCESS_TOKEN_TTL_ENV",
    "AuthUser",
    "AuthenticatedIdentity",
    "DEFAULT_ACCESS_TOKEN_TTL_SECONDS",
    "DEMO_PASSWORD_ENV",
    "ErrorResponse",
    "JWT_ALGORITHM",
    "JWT_SECRET_ENV",
    "LoginRequest",
    "LoginResponse",
    "authenticate_customer",
    "create_access_token",
    "get_authenticated_customer_id",
    "get_authenticated_identity",
    "revoke_access_token",
    "router",
]
