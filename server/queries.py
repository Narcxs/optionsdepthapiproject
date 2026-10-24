"""Generic "latest snapshot of a series" query.

Every data table shares the same shape: several rows carry the same
`response_id` (one captured API response = one grid), and `received_at`
orders the responses in time. So "the latest series matching these
filters" is always the same query, parameterized by table and columns —
not duplicated eight times.

`table` is always one of the constants in `server.main`, never a value
coming from the request, so there is no SQL injection surface despite
the f-string.
"""

from __future__ import annotations

import sqlite3
from typing import Any


class SeriesNotFound(Exception):
    """No row in the table matches the given filters."""


def latest_series(
    connection: sqlite3.Connection,
    table: str,
    filters: dict[str, str | None],
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[str] = []

    for column, value in filters.items():
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    found = connection.execute(
        f"SELECT response_id FROM {table} {where} ORDER BY received_at DESC LIMIT 1",  # noqa: S608
        params,
    ).fetchone()

    if found is None:
        raise SeriesNotFound(f"No data in '{table}' for the given filters")

    rows = connection.execute(
        f"SELECT * FROM {table} WHERE response_id = ?",  # noqa: S608
        (found["response_id"],),
    ).fetchall()

    dicts = [dict(row) for row in rows]

    head = dicts[0]

    return {
        "response_id": head.get("response_id"),
        "trade_date": head.get("trade_date"),
        "timeslot": head.get("timeslot"),
        "received_at": head.get("received_at"),
        "count": len(dicts),
        "rows": dicts,
    }
