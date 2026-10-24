"""Read-only access to `data/od.db`.

The scraper (`python main.py live`) is the only writer and runs in WAL
mode (`src/store.py: open_database`), which is exactly what allows this
process to read concurrently without locking it out. So the connection
here is opened **read-only** (`mode=ro`): a bug in the API must never be
able to touch the archive.

One connection per request, deliberately not pooled. Measured against a
real capture: opening and closing a connection costs ~0.05ms, against
several ms for an unindexed query — pooling would not be a measurable
win. It would also be actively harmful on this project's deployment
target (a Windows VPS, see `supervisor.py`): `supervisor.wipe_session_data`
deletes `data/od.db` after every trading session, and on Windows a file
still held open by another process — a long-lived pooled connection in
this server — cannot be deleted (`PermissionError: [WinError 32]`,
confirmed by testing). Closing the connection at the end of every
request, as `connection_dependency` does, is what keeps that delete
working.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException, status

from server.config import settings


def _open() -> sqlite3.Connection:
    path = Path(settings.database)

    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database not found: {path} (has the scraper run yet?)",
        )

    uri = f"file:{path.resolve().as_posix()}?mode=ro"

    try:
        connection = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=5)
    except sqlite3.Error as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Cannot open the database: {error}",
        ) from error

    connection.row_factory = sqlite3.Row

    # `query_only` is a second guarantee beyond `mode=ro` in the URI: a
    # bug here must never reach the writer's file. `mmap_size` lets the
    # OS serve reads straight from its page cache instead of going
    # through SQLite's own buffer for every page — helps the `/series`
    # routes, which scan a whole table to aggregate (no index can help a
    # GROUP BY over everything). Both are cheap to set on every
    # connection: see `connection_dependency`'s docstring for why a new
    # connection per request, rather than a pooled one, is the right
    # call here.
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA mmap_size = 268435456")

    return connection


def connection_dependency() -> Iterator[sqlite3.Connection]:
    connection = _open()

    try:
        yield connection
    finally:
        connection.close()
