"""Tracks issued Bearer tokens, so one can be revoked on its own.

A plain JWT (`server/auth.py`'s signature + `exp` check) can only ever be
revoked all at once, by rotating `OD_JWT_SECRET` — which kills every
token everyone holds, not just the one you meant to cut off. This table
is the second, independent check that makes revoking *one* token
possible: each token carries a `jti` (a random id, unique to that one
token), and a request is only accepted if its `jti` is both registered
here and not marked revoked.

Lives outside `data/`, deliberately: `supervisor.wipe_session_data`
deletes `data/network.jsonl`, `decrypted.jsonl` and `od.db*` — the
scraped market data, meant to be thrown away every day — not this. A
friend's still-valid, not-yet-expired token must not stop working at
the next market close for a reason that has nothing to do with its own
expiration.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TOKENS_DATABASE = "tokens.db"


def open_tokens_db(path: str = DEFAULT_TOKENS_DATABASE) -> sqlite3.Connection:
    parent = Path(path).parent

    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, check_same_thread=False)

    connection.execute("PRAGMA journal_mode = WAL")

    connection.row_factory = sqlite3.Row

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tokens (
            jti         TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            issued_at   TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            revoked_at  TEXT
        )
        """
    )

    return connection


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def issue(connection: sqlite3.Connection, jti: str, name: str, expires_at: str) -> None:
    connection.execute(
        "INSERT INTO tokens (jti, name, issued_at, expires_at) VALUES (?, ?, ?, ?)",
        (jti, name, _now(), expires_at),
    )
    connection.commit()


def is_live(connection: sqlite3.Connection, jti: str) -> bool:
    """Does this token still count as valid?

    An unknown `jti` — one never issued by this server, or issued
    before the store was reset — is treated as **not** live. Once
    individual revocation exists, a signature that merely checks out is
    no longer enough: revoking a token only means something if holding
    a token nobody registered does not grant access either.
    """
    row = connection.execute("SELECT revoked_at FROM tokens WHERE jti = ?", (jti,)).fetchone()

    return row is not None and row["revoked_at"] is None


def revoke_by_jti(connection: sqlite3.Connection, jti: str) -> bool:
    """Revokes one specific token. Returns whether it was live to begin with."""
    cursor = connection.execute(
        "UPDATE tokens SET revoked_at = ? WHERE jti = ? AND revoked_at IS NULL",
        (_now(), jti),
    )
    connection.commit()

    return cursor.rowcount > 0


def revoke_by_name(connection: sqlite3.Connection, name: str) -> int:
    """Revokes every currently-live token issued under this name.

    "Cut off Alice's access" should not require knowing which of
    possibly several tokens she is currently using.
    """
    cursor = connection.execute(
        "UPDATE tokens SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
        (_now(), name),
    )
    connection.commit()

    return cursor.rowcount


def list_tokens(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT jti, name, issued_at, expires_at, revoked_at FROM tokens ORDER BY issued_at DESC"
    ).fetchall()
