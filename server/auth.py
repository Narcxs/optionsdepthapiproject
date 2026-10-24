"""Bearer token check: a short-lived JWT, signed with `OD_JWT_SECRET`.

Replaces the earlier static `X-API-Key`. The token is not a credential on
its own — it is minted by `tools/generate_token.py` (the "bot") from the
same shared secret this server verifies against, and it expires on its
own: a leaked token stops working once `exp` passes, instead of being
valid forever until someone notices and rotates a static key.
"""

from __future__ import annotations

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.config import settings

# The algorithm is symmetric (HMAC): the same `OD_JWT_SECRET` both signs
# (in the bot) and verifies (here). There is no asymmetric key pair to
# manage for a single trusted bot issuing tokens for a single server.
ALGORITHM = "HS256"

# Binds a token to this specific purpose: `jwt.decode(audience=...)`
# rejects a token minted for anything else that happened to be signed
# with the same secret, instead of accepting it just because the
# signature checks out.
AUDIENCE = "od-api"

_bearer = HTTPBearer(auto_error=False)


def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    if not settings.jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OD_JWT_SECRET is not configured on the server",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token (Authorization: Bearer <token>)",
        )

    try:
        jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
        )
    except jwt.ExpiredSignatureError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired — generate a new one with tools/generate_token.py",
        ) from error
    except jwt.InvalidTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {error}",
        ) from error
