"""Do the two databases have the same rows?

One was written by the Python program, the other by the Rust program, from
the **same** decrypted file. If the two implementations are the same thing,
the tables must coincide row by row: not just the counts, the rows.

    python tools/check_same_rows.py ..\\rust_od\\data\\od.db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

# The tables and the columns to compare, in order.
TABLES: dict[str, tuple[str, ...]] = {
    "responses": (
        "id",
        "received_at",
        "captured_at",
        "endpoint",
        "ticker",
        "trade_date",
        "timeslot",
        "exposure",
        "side",
        "rows_loaded",
        "url",
    ),
    "breakdown_by_strike": (
        "response_id",
        "received_at",
        "timeslot",
        "trade_date",
        "ticker",
        "exposure",
        "side",
        "strike",
        "call",
        "put",
        "net",
    ),
    "breakdown_by_expiration": (
        "response_id",
        "timeslot",
        "trade_date",
        "exposure",
        "side",
        "expiration",
        "option_symbol",
        "call",
        "put",
        "net",
    ),
    "depth_view": (
        "response_id",
        "timeslot",
        "trade_date",
        "exposure",
        "side",
        "strike",
        "expiration",
        "option_symbol",
        "call",
        "put",
        "net",
    ),
    "heatmaps": (
        "response_id",
        "timeslot",
        "trade_date",
        "metric",
        "measurement",
        "time_slot",
        "price",
        "value",
    ),
    "volatility_table": (
        "response_id",
        "timeslot",
        "trade_date",
        "expiry",
        "exp_date",
        "strike",
        "iv",
        "od_value",
        "percentile",
    ),
    "volatility_surface": (
        "response_id",
        "timeslot",
        "trade_date",
        "snapshot_time",
        "expiration",
        "dte",
        "strike",
        "iv",
    ),
    "volatility_slices": (
        "response_id",
        "kind",
        "timeslot",
        "trade_date",
        "snapshot_time",
        "anchor_expiration",
        "anchor_strike",
        "expiration",
        "strike",
        "trace_index",
        "iv",
    ),
    "candlesticks": (
        "response_id",
        "trade_date",
        "measurement",
        "frequency",
        "candle_time",
        "open",
        "high",
        "low",
        "close",
    ),
}


def rows_of(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    ids: set[str],
) -> set:
    """The rows of a table, for the responses indicated.

    They are filtered by `response_id`: the Python database is the one of
    the live capture and grows with every response, so the rows Rust has
    never seen are not a divergence. The archive is the exception: its key
    is called `id`.
    """
    selected = ", ".join(columns)

    if not ids:
        return set()

    key = "id" if table == "responses" else "response_id"

    placeholders = ", ".join("?" for _ in ids)

    return {
        tuple(row)
        for row in connection.execute(
            f"SELECT {selected} FROM {table} WHERE {key} IN ({placeholders})",
            tuple(ids),
        )
    }


def main(argv: list[str]) -> int:
    mine = Path("data/od.db")
    theirs = Path(argv[1] if len(argv) > 1 else "../rust_od/data/od.db")

    if not theirs.exists():
        print(f"❌ Rust database not found: {theirs}")

        return 1

    a = sqlite3.connect(f"file:{mine}?mode=ro", uri=True)

    b = sqlite3.connect(f"file:{theirs}?mode=ro", uri=True)

    failures = 0

    # The responses the two databases have **in common**. The Python
    # database is the one of the live capture and grows: the responses
    # Rust has never seen are not a divergence.
    mine_ids = {row[0] for row in a.execute("SELECT id FROM responses")}
    theirs_ids = {row[0] for row in b.execute("SELECT id FROM responses")}

    shared = mine_ids & theirs_ids

    print(
        f"responses: {len(mine_ids)} in Python · {len(theirs_ids)} in Rust · "
        f"{len(shared)} shared"
    )
    print(
        f"(Python only: {len(mine_ids - theirs_ids)} · "
        f"Rust only: {len(theirs_ids - mine_ids)})"
    )
    print("")

    print(f"{'table':<26} {'Python':>8} {'Rust':>8}  {'shared':>10}  result")
    print("-" * 70)

    for table, columns in TABLES.items():
        try:
            left = rows_of(a, table, columns, shared)
        except sqlite3.Error as error:
            print(f"{table:<26} not readable: {error}")

            failures += 1

            continue

        right = rows_of(b, table, columns, shared)

        common = left & right

        ok = left == right

        if not ok:
            failures += 1

        print(
            f"{table:<26} {len(left):>8} {len(right):>8}  {len(common):>10}  "
            f"{'✅' if ok else '❌'}"
        )

        if not ok:
            only_left = list(left - right)[:2]
            only_right = list(right - left)[:2]

            print(f"      Python only: {only_left}")
            print(f"      Rust only:   {only_right}")

    a.close()
    b.close()

    print("")

    if failures:
        print(f"❌ {failures} tables differ")

        return 1

    print("✅ The two implementations wrote the same rows, row by row.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
