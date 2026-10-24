"""Server settings, read from the environment.

Nothing here is hard-coded: the API key and the database path come from
`.env` (or whatever sets the environment in production), so the same
code runs unmodified on a laptop and on the VPS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    database: str


def load_settings() -> Settings:
    return Settings(
        api_key=os.environ.get("OD_API_KEY") or None,
        database=os.environ.get("OD_DATABASE", "data/od.db"),
    )


settings = load_settings()
