"""Server settings, read from the environment.

Nothing here is hard-coded: the signing secret and the database path
come from `.env` (or whatever sets the environment in production), so
the same code runs unmodified on a laptop and on the VPS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # The HMAC secret shared between this server (which verifies tokens,
    # see `server/auth.py`) and `tools/generate_token.py` (the "bot",
    # which signs them) — the same secret both sides need, never sent
    # over the wire itself. Whoever holds it can mint a working token at
    # will, so it belongs in `.env`, not in a header on every request.
    jwt_secret: str | None
    database: str
    tokens_database: str


def load_settings() -> Settings:
    return Settings(
        jwt_secret=os.environ.get("OD_JWT_SECRET") or None,
        database=os.environ.get("OD_DATABASE", "data/od.db"),
        tokens_database=os.environ.get("OD_TOKENS_DATABASE", "tokens.db"),
    )


settings = load_settings()
