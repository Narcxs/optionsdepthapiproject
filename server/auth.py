"""Bearer token check: a short-lived, individually revocable JWT.

Two independent checks, both must pass:

  1. the JWT's signature and `exp` claim (`jwt.decode`, below) — proves
     the token was minted by someone holding `OD_JWT_SECRET` and has
     not expired;
  2. the token's `jti` is registered and not revoked in `tokens.db`
     (`server/tokens_store.py`) — proves this *specific* token has not
     been individually cut off.

The second check is what makes "revoke just my friend's token" possible
without rotating the shared secret, which would kill every token at
once, including your own.
"""

from __future__ import annotations

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.config import settings
from server.tokens_store import is_live, open_tokens_db

# The algorithm is symmetric (HMAC): the same `OD_JWT_SECRET` both signs
# (in tools/generate_token.py) and verifies (here). There is no
# asymmetric key pair to manage for a single trusted bot issuing tokens
# for a single server.
ALGORITHM = "HS256"

# Binds a token to this specific purpose: `jwt.decode(audience=...)`
# rejects a token minted for anything else that happened to be signed
# with the same secret, instead of accepting it just because the
# signature checks out.
AUDIENCE = "od-api"

_bearer = HTTPBearer(auto_error=False)

# Opened once, not per request: unlike `data/od.db` (see `server/db.py`),
# this file is never deleted out from under a running process — nothing
# in `supervisor.py` touches it — so there is no Windows file-lock
# concern to design around, and a persistent connection avoids an
# open/close per request for no benefit.
_tokens_db = open_tokens_db(settings.tokens_database)


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
        payload = jwt.decode(
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

    jti = payload.get("jti")

    # Every token `tools/generate_token.py` mints carries one — this
    # only trips for a token signed by hand or by an older version of
    # that script, before revocation existed.
    if not jti or not is_live(_tokens_db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked or is not recognized",
        )
