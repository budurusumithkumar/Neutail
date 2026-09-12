"""Minimal JWT authentication boundary for the Neu.Tail chat API."""

from __future__ import annotations

import os
from typing import Annotated, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


JWT_SECRET_ENV = "NEUTAIL_JWT_SECRET"
JWT_ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


async def get_authenticated_customer_id(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(_bearer)
    ],
) -> str:
    """Validate a signed access token and return its customer subject."""

    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A Bearer access token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    secret = os.getenv(JWT_SECRET_ENV)
    if not secret or len(secret.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT authentication is not securely configured",
        )

    try:
        claims = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The access token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    customer_id = claims.get("sub")
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The access token has no valid customer subject",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return customer_id.strip()


__all__ = [
    "JWT_ALGORITHM",
    "JWT_SECRET_ENV",
    "get_authenticated_customer_id",
]
