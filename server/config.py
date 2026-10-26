"""Server settings, read from the environment.

Nothing here is hard-coded: the signing secret and the database path
come from `.env` (or whatever sets the environment in production), so
the same code runs unmodified on a laptop and on the VPS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Loads `KEY=value` lines from `.env` into the process environment.

    Only fills in a key that is not already set there: a value the
    deployment itself provides (a real environment variable, systemd's
    `EnvironmentFile=`, ...) always wins over `.env`, which is a
    local/manual fallback, not an override.

    Linux's systemd unit (`deploy/od-api.service`) sets every `.env`
    key via `EnvironmentFile=` before this process even starts, so this
    is a no-op there. Windows has no equivalent — nothing else fills
    `os.environ` from `.env` before this module reads it — so this runs
    unconditionally at import time instead of being left to whichever
    launcher starts the process (the Scheduled Task created by
    `deploy/create-api-task.bat`, in particular).
    """
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()

        if key and key not in os.environ:
            os.environ[key] = value.strip()


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
    _load_dotenv()

    return Settings(
        jwt_secret=os.environ.get("OD_JWT_SECRET") or None,
        database=os.environ.get("OD_DATABASE", "data/od.db"),
        tokens_database=os.environ.get("OD_TOKENS_DATABASE", "tokens.db"),
    )


settings = load_settings()
