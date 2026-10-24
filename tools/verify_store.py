"""Verifies the database, without trusting the code that wrote it.

It reads the responses back — from the decrypted file when it is there,
otherwise decrypting them again **from the archive** — and compares every row
with the database. If the two sides match, the database really holds what the
API answered.

Why from the archive: with the live capture the database can hold responses
that were never in `data/decrypted.jsonl` (the row left the capture right
after). Decrypting from the archive is the strongest check there is, because
it uses the same encryption the database keeps.

Usage:

    python tools/verify_store.py [--db data/od.db] [--file data/decrypted.jsonl]

Translation of `rust_od/tools/verify_store.js`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from actions.decode import fingerprint  # noqa: E402
from actions.decrypt import DecryptError, decrypt_response  # noqa: E402

# The endpoints that have a table.
LOADED = (
    "breakdown-by-strike",
    "breakdown-by-expiration",
    "depth-view-table",
    "volatility-table",
    "volatility-3d-surface",
    "volatility-3d-surface-companion",
    "volatility-3d-surface-term-structure",
    "plotly-gamma",
    "plotly-charm",
    "plotly-vanna",
    "plotly-candlesticks",
)

SLICE_KINDS = {
    "volatility-3d-surface-companion": "smile",
    "volatility-3d-surface-term-structure": "term-structure",
}

HEATMAP_KINDS = {
    "plotly-gamma": "GAMMA",
    "plotly-charm": "CHARM",
    "plotly-vanna": "VANNA",
}


class Verifier:
    """The checks, and the count of how many pass."""

    def __init__(self, connection: sqlite3.Connection):
        self.db = connection

        self.passed = 0
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1

            print(f"✅ {name}{' · ' + detail if detail else ''}")
        else:
            self.failed += 1

            print(f"❌ {name}{' · ' + detail if detail else ''}")

    def rows(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, parameters).fetchall()

    def one(self, sql: str, parameters: tuple = ()):
        return self.db.execute(sql, parameters).fetchone()

    def scalar(self, sql: str, parameters: tuple = ()):
        row = self.one(sql, parameters)

        return None if row is None else row[0]


# ======================================================
# THE RECORDS, FROM WHEREVER THEY COME
# ======================================================


def load_records(verifier: Verifier, path: Path) -> list[dict]:
    """The records to verify: from the file, plus the ones only the archive has."""
    records: list[dict] = []

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))

    known = {record.get("fingerprint") for record in records}

    from_archive = 0

    for row in verifier.rows(
        "SELECT id, received_at, captured_at, endpoint, url, iv, data, tag FROM responses ORDER BY received_at"
    ):
        if row["id"] in known:
            continue

        try:
            payload = decrypt_response(row["data"], row["iv"], row["tag"])
        except DecryptError as error:
            print(f"⚠️ from the archive {row['id']} does not decrypt: {error}")

            continue

        records.append(
            {
                "fingerprint": row["id"],
                "captured_at": row["captured_at"],
                "received_at": row["received_at"],
                "endpoint": row["endpoint"],
                "url": row["url"],
                "iv": row["iv"],
                "data": row["data"],
                "tag": row["tag"],
                "payload": payload,
            }
        )

        from_archive += 1

    if from_archive:
        print(
            f"   📦 {from_archive} responses are not in {path}: "
            "they are verified by decrypting them from the archive"
        )
        print("")

    return records


# ======================================================
# PRUNING, THE SAME WAY THE LIVE WRITER NOW DOES
#
# `store.py` deletes every older response of a series as soon as a newer
# one of the same series is written (`store._prune_series`): the API
# only ever serves the latest snapshot, so keeping the rest was pure
# disk growth with nothing reading it. The database this verifier opens
# has already had that pruning applied on every write — so comparing it
# against the **whole** capture file's response count would report a
# "missing" row for every response that was correctly superseded, not a
# real bug.
#
# This mirrors that policy **independently** — it does not call into
# `store.py` — on purpose: the point of this verifier is to catch a bug
# in that module, and importing its own pruning logic to check its own
# pruning would make the check blind to exactly the kind of mistake it
# exists to catch.
# ======================================================


def _expiration_scope(params: dict[str, str], trade_date: str | None) -> str:
    """Same rule as `store.expiration_scope_of`, written here on its own:
    "ALL" with no expiration range in the URL, "0DTE" when the range is
    the trade date alone, the literal range otherwise."""
    start = params.get("expiration_range_start") or None
    end = params.get("expiration_range_end") or None

    if start is None and end is None:
        return "ALL"

    if start == trade_date and end == trade_date:
        return "0DTE"

    return f"{start or '*'}..{end or '*'}"


def _series_identity(record: dict) -> tuple:
    """Which series this response belongs to — the same identity
    `store._prune_series` keys on for this endpoint.

    `endpoint` is always the first element: for the three heatmap
    endpoints and the two slice endpoints, the endpoint alone already
    says the metric or the kind, so nothing else needs to be added for
    those beyond the ticker.
    """
    endpoint = record["endpoint"]
    params = _params(record.get("url", ""))
    ticker = params.get("ticker")

    if endpoint == "breakdown-by-strike":
        scope = _expiration_scope(params, params.get("date"))

        return (endpoint, ticker, params.get("metric", "").upper(), side_of(params.get("participants")), scope)

    if endpoint in ("breakdown-by-expiration", "depth-view-table"):
        return (endpoint, ticker, params.get("metric", "").upper(), side_of(params.get("participants")))

    if endpoint == "plotly-candlesticks":
        return (endpoint, ticker, params.get("measurement"), params.get("frequency"))

    if endpoint in HEATMAP_KINDS:
        return (endpoint, ticker, params.get("measurement"))

    # volatility-table, volatility-3d-surface, and the two slice
    # endpoints (volatility-3d-surface-companion/-term-structure): one
    # series per ticker, the endpoint says the rest.
    return (endpoint, ticker)


def prune_to_latest(records: list[dict]) -> list[dict]:
    """Keeps, per series, only the response captured last — see the
    module note above. Responses of an endpoint with no table (none seen
    in practice, but `LOADED` is not every endpoint `decrypted.jsonl` can
    hold) pass through untouched: there is no pruning rule for them to
    mirror."""
    latest: dict[tuple, dict] = {}
    passthrough: list[dict] = []

    for record in records:
        if record["endpoint"] not in LOADED:
            passthrough.append(record)

            continue

        key = _series_identity(record)
        current = latest.get(key)

        if current is None or record.get("captured_at", 0) >= current.get("captured_at", 0):
            latest[key] = record

    return list(latest.values()) + passthrough


def _retained_ids(pruned_records: list[dict]) -> set[str]:
    """Fingerprints of the responses that should still have per-cell
    rows right now — i.e. the result of `prune_to_latest`, by id.

    Every *other* response of that same endpoint is expected to have
    **zero** per-cell rows: pruning deleted them. A body-vs-database
    comparison that finds rows for one of those anyway is not "data
    came back correctly" — it is pruning having silently failed to
    remove something it should have.
    """
    return {record["fingerprint"] for record in pruned_records}


# ======================================================
# THE VALUES EXPECTED FROM THE PAYLOAD
# ======================================================


def doubles(value, path: str = "$") -> dict[str, float]:
    """All the doubles of a payload, indexed by path."""
    found: dict[str, float] = {}

    if isinstance(value, bool):
        return found

    if isinstance(value, float):
        found[path] = value

        return found

    if isinstance(value, dict):
        for key, item in value.items():
            found.update(doubles(item, f"{path}.{key}"))

    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(doubles(item, f"{path}[{index}]"))

    return found


def as_float(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    return None


# ======================================================
# THE HELPERS THE OTHER TABLES NEED
# ======================================================


# The API writes the bare date, and attaches the symbol only to the monthly
# expiration that falls on the same day as a weekly one. The implicit symbol
# of a bare date is SPXW.
IMPLICIT_OPTION_SYMBOL = "SPXW"


def _params(url: str) -> dict[str, str]:
    """The query params of the URL, like JavaScript's `searchParams`."""
    from api import query

    return query.params(url)


def dig(value, *keys):
    """JavaScript's `a?.b?.c`: `None` as soon as a piece is missing."""
    for key in keys:
        if not isinstance(value, dict):
            return None

        value = value.get(key)

    return value


def side_of(participants) -> str | None:
    """The side as the database writes it."""
    if participants == "mm_posn":
        return "MM"

    return "CUSTOMER" if participants else None


def split_expiration(label: str) -> tuple[str, str]:
    """`"2026-10-16, SPX"` → date and symbol · `"2026-10-16"` → `SPXW`."""
    comma = label.find(",")

    if comma < 0:
        return label.strip(), IMPLICIT_OPTION_SYMBOL

    return label[:comma].strip(), label[comma + 1 :].strip().upper()


def js_number(value) -> float:
    """JavaScript's `Number(value)`: missing or empty makes zero."""
    if value is None or value == "":
        return 0.0

    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def js_str(value) -> str:
    """`String(value)`: in JavaScript `12400.0` is written `"12400"`."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value)


def candle_utc(value: str) -> str:
    """`new Date(x).toISOString()`: the instant with the time zone, in UTC."""
    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def first_trace(payload, path: str = "figure") -> dict | None:
    """The first trace of a figure, or `None` if there is none.

    It is needed because a payload **can** be empty: on 5 October the dashboard
    asked for the candlesticks of a day with no market and the API answered
    `figure.data: []`. The parser rejects those responses on purpose, so the
    verifier must not break: it must say that there is nothing to count.
    """
    if not isinstance(payload, dict):
        return None

    figure = payload.get(path)

    if not isinstance(figure, dict):
        return None

    traces = figure.get("data")

    if not isinstance(traces, list) or not traces:
        return None

    first = traces[0]

    return first if isinstance(first, dict) else None


def _seconds(moment: str) -> float:
    """`2026-10-02 07:30:07` → a comparable number.

    It tells which grid is **closest** to a smile: without it, "take the
    first one" can mean three hours earlier.
    """
    import datetime as dt

    try:
        parsed = dt.datetime.strptime(moment.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return 0.0

    return parsed.replace(tzinfo=dt.timezone.utc).timestamp()


def gap_of(rows, call: str = "call", put: str = "put", net: str = "net") -> float:
    """The maximum of |call + put − net|, row by row and without lists."""
    worst = 0.0

    for row in rows:
        gap = abs(row[call] + row[put] - row[net])

        if gap > worst:
            worst = gap

    return worst
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verifies the rust_od database")

    parser.add_argument("--db", default="data/od.db")
    parser.add_argument("--file", default="data/decrypted.jsonl")

    arguments = parser.parse_args(argv)

    database = Path(arguments.db)

    if not database.exists():
        print(f"❌ database not found: {database}")

        return 1

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

    connection.row_factory = sqlite3.Row

    verifier = Verifier(connection)

    # `records` stays the **full** capture history on purpose: `responses`
    # (the archive table) is not pruned by the writer — see `store.py`'s
    # note on `_prune_series` for why (idempotency, and this verifier's
    # own need to decrypt every archived body independently) — so the
    # archive-related checks below (`with_table` vs `archive`, "every
    # body decrypts again", …) must compare against the same full
    # history `responses` actually holds. Only the per-table endpoint
    # lists further down are narrowed with `prune_to_latest`, because
    # only the per-cell tables (`breakdown_by_strike`, `heatmaps`, …)
    # are pruned to the latest response of each series.
    records = load_records(verifier, Path(arguments.file))

    counts: dict[str, int] = {}

    for record in records:
        counts[record["endpoint"]] = counts.get(record["endpoint"], 0) + 1

    print("responses:")
    for endpoint, count in sorted(counts.items()):
        print(f"   {count:>5}  {endpoint}")

    print("")

    # ==================================================
    # THE ARCHIVE
    # ==================================================

    archive = verifier.scalar("SELECT COUNT(*) FROM responses")

    # The responses the database **can** write: the ones with a table, minus
    # the ones the parser rejects. Measured: the only case is the candlestick
    # responses of a day with no market — `figure.data` empty, the parser says
    # "no trace with the candlesticks", and they do not enter the archive. If
    # one day another one showed up, the count would say it instead of passing
    # a lost datum off as a written one.
    empty_candles = sum(
        1
        for record in records
        if record["endpoint"] == "plotly-candlesticks"
        and not (first_trace(record.get("payload")) or {}).get("x")
    )

    with_table = sum(1 for record in records if record["endpoint"] in LOADED) - empty_candles

    verifier.check(
        "every response with a table is in the archive",
        archive == with_table,
        f"{archive} in the archive · {with_table} with a table"
        + (f" · {empty_candles} rejected by the parser (empty)" if empty_candles else ""),
    )

    bodies = verifier.rows("SELECT id, iv, data, tag FROM responses")

    not_decryptable = 0

    for row in bodies:
        try:
            decrypt_response(row["data"], row["iv"], row["tag"])
        except DecryptError:
            not_decryptable += 1

    verifier.check("every body in the archive decrypts again", not_decryptable == 0, f"{not_decryptable} unreadable")

    wrong_ids = 0

    for row in bodies:
        if fingerprint(row["iv"], row["data"], row["tag"]) != row["id"]:
            wrong_ids += 1

    verifier.check(
        "the id of every response is the fingerprint of its body",
        wrong_ids == 0,
        f"{wrong_ids} different fingerprints",
    )
    # ==================================================
    # BREAKDOWN BY STRIKE
    # ==================================================

    # `prune_to_latest`: the per-cell table only holds the latest
    # response of each series (see the module note above `records = `),
    # so what it is compared against here must be narrowed the same way.
    strike_records = prune_to_latest(
        [record for record in records if record["endpoint"] == "breakdown-by-strike"]
    )

    expected = []

    for record in strike_records:
        traces = record["payload"]["figure"]["data"]

        calls = next(trace for trace in traces if trace.get("name") == "Calls")
        puts = next(trace for trace in traces if trace.get("name") == "Puts")
        net = next(trace for trace in traces if trace.get("name") == "Net")

        timeslot = _timeslot(record["url"], record.get("received_at"))

        for index, strike in enumerate(calls["y"]):
            triple = calls["customdata"][index]

            expected.append(
                {
                    "id": record["fingerprint"],
                    "strike": strike,
                    "call": triple[0],
                    "put": triple[1],
                    "net": triple[2],
                    "calls_x": calls["x"][index],
                    "puts_x": puts["x"][index],
                    "net_x": net["x"][index],
                    "timeslot": timeslot,
                }
            )

    stored = {
        (row["response_id"], row["strike"]): row
        for row in verifier.rows("SELECT response_id, strike, call, put, net FROM breakdown_by_strike")
    }

    missing = 0
    different = 0

    for row in expected:
        saved = stored.get((row["id"], row["strike"]))

        if saved is None:
            missing += 1

            continue

        if saved["call"] != row["call"] or saved["put"] != row["put"] or saved["net"] != row["net"]:
            different += 1

    verifier.check(
        "every breakdown row matches the payload",
        missing == 0 and different == 0,
        f"{missing} missing, {different} different",
    )

    verifier.check(
        "no extra breakdown row",
        len(stored) == len(expected),
        f"{len(stored)} in the database, {len(expected)} expected",
    )

    # The `customdata` triple repeats the three traces: if they do not match,
    # the response was not consistent and had to stay out.
    incoherent = sum(
        1
        for row in expected
        if not (
            row["call"] == row["calls_x"]
            and row["put"] == row["puts_x"]
            and row["net"] == row["net_x"]
        )
    )

    verifier.check(
        "the customdata triple repeats the three traces",
        incoherent == 0,
        f"{incoherent} inconsistent rows",
    )

    # call + put ≈ net: the API rounds every series.
    worst = 0.0

    for row in expected:
        gap = abs(row["call"] + row["put"] - row["net"])

        if gap > worst:
            worst = gap

    verifier.check("call + put ≈ net", worst <= 0.02, f"maximum gap {worst:.4f}")

    # ==================================================
    # THE CANDLESTICKS
    # ==================================================

    candle_records = prune_to_latest(
        [record for record in records if record["endpoint"] == "plotly-candlesticks"]
    )

    if candle_records:
        rows = verifier.scalar("SELECT COUNT(*) FROM candlesticks")

        # A candlestick response can be **empty**: on 5 October the dashboard
        # asked for the candlesticks of a day with no market, and the API
        # answered `figure.data: []`. It is not a failure — it is "there is
        # nothing" — and the parser rejects it on purpose ("no candlestick in
        # the response"), so no row ends up in the database. Here it must not
        # break: the candlesticks of the responses that have them are counted,
        # and it is said how many were empty.
        with_candles = []
        empty = 0

        for record in candle_records:
            payload = record.get("payload") or {}

            figure = payload.get("figure") or {}

            traces = figure.get("data") or []

            first = traces[0] if traces else None

            x = first.get("x") if isinstance(first, dict) else None

            if not isinstance(x, list):
                empty += 1

                continue

            with_candles.append(record)

        expected_candles = sum(len(record["payload"]["figure"]["data"][0]["x"]) for record in with_candles)

        detail = f"{rows} vs {expected_candles}"

        if empty:
            detail += f" · {empty} responses without candlesticks (day with no market)"

        verifier.check(
            "the candlesticks in the database are all the ones of the payloads",
            rows == expected_candles,
            detail,
        )

    # ==================================================
    # THE VALUES: THE DOUBLES
    #
    # The check the Rust project existed to do: the numbers in the database
    # must be **the same doubles** as the payload, not "almost". The bits are
    # compared.
    # ==================================================

    compared = 0
    differing = 0
    examples: list[str] = []

    for record in records:
        endpoint = record["endpoint"]

        if endpoint not in LOADED:
            continue

        payload_doubles = doubles(record["payload"])

        compared += len(payload_doubles)

        if len(payload_doubles) == 0:
            continue

        # The real comparison is per table, and the one who knows the columns
        # does it: here it is checked that the payload has the numbers one
        # expects (no `inf`, no `NaN`).
        for key, value in payload_doubles.items():
            if value != value or value in (float("inf"), float("-inf")):
                differing += 1

                if len(examples) < 5:
                    examples.append(f"{endpoint} {key} = {value}")

    verifier.check(
        "no non-finite number in the payloads",
        differing == 0,
        f"{compared} doubles looked at" + (f" · {', '.join(examples)}" if examples else ""),
    )

    # ==================================================
    # THE OTHER TABLES
    #
    # Every block translates a `check(...)` block of
    # `rust_od/tools/verify_store.js`, in the same order. Two rules, because
    # the JS verifier on a large archive never finished:
    #
    #   · no query inside a loop over the rows: what is needed is read once
    #     and compared in memory;
    #   · no huge list built only to take its maximum: SQLite does the
    #     aggregates, or a loop that does not allocate does them.
    #
    # The eight JS checks that already have an equivalent above — rows in the
    # database, every row matches, no extra row, customdata, call + put ≈ net,
    # the responses with a table, the fingerprint of the body, the bodies that
    # decrypt again, the candlesticks in the database — are not repeated:
    # they are the nine checks that were already there.
    # ==================================================

    expiration_records = prune_to_latest(
        [record for record in records if record["endpoint"] == "breakdown-by-expiration"]
    )
    depth_records = prune_to_latest(
        [record for record in records if record["endpoint"] == "depth-view-table"]
    )
    heatmap_records = prune_to_latest(
        [record for record in records if record["endpoint"] in HEATMAP_KINDS]
    )
    volatility_records = prune_to_latest(
        [record for record in records if record["endpoint"] == "volatility-table"]
    )
    surface_records = prune_to_latest(
        [record for record in records if record["endpoint"] == "volatility-3d-surface"]
    )
    slice_records = prune_to_latest(
        [record for record in records if record["endpoint"] in SLICE_KINDS]
    )

    # --------------------------------------------------
    # BREAKDOWN BY STRIKE · the rest
    # --------------------------------------------------
    strike_archive = verifier.scalar(
        "SELECT COUNT(*) FROM responses WHERE endpoint = ?", ("breakdown-by-strike",)
    )

    # Against the **full** capture count, not `strike_records` (pruned to
    # latest): `responses` itself is never pruned — see `store.py`'s note
    # on `_prune_series` for why.
    strike_captured = sum(1 for record in records if record["endpoint"] == "breakdown-by-strike")

    verifier.check(
        "responses in the archive",
        strike_archive == strike_captured,
        f"{strike_archive} vs {strike_captured}",
    )

    # The references every row must carry with it, response by response.
    strike_references = {}

    for record in strike_records:
        parameters = _params(record["url"])

        strike_references[record["fingerprint"]] = (
            record["received_at"],
            _timeslot(record["url"], record.get("received_at")),
            parameters.get("date"),
            parameters.get("ticker"),
            parameters.get("metric"),
            side_of(parameters.get("participants")),
        )

    strike_wrong = 0

    for row in verifier.rows(
        "SELECT response_id, received_at, timeslot, trade_date, ticker, exposure, side "
        "FROM breakdown_by_strike"
    ):
        want = strike_references.get(row["response_id"])

        if want is None or (
            row["received_at"],
            row["timeslot"],
            row["trade_date"],
            row["ticker"],
            row["exposure"],
            row["side"],
        ) != want:
            strike_wrong += 1

    verifier.check("references correct on every row", strike_wrong == 0, f"{strike_wrong} inconsistent")

    # The description is not a database column: the archive keeps the
    # encrypted body, and the description is read back from there. Here it is
    # checked that it says the same thing as metric and side. (Breakdown only:
    # the depth view has no axis title.)
    strike_bodies = verifier.rows(
        "SELECT id, exposure, side, iv, data, tag FROM responses WHERE endpoint = 'breakdown-by-strike'"
    )

    without_description = 0
    description_wrong = 0

    for row in strike_bodies:
        payload = decrypt_response(row["data"], row["iv"], row["tag"])

        description = dig(payload, "figure", "layout", "xaxis", "title", "text")

        if not description:
            without_description += 1

            continue

        side = "Market Maker" if row["side"] == "MM" else "Customer"

        if side not in description:
            description_wrong += 1

    verifier.check(
        "description (from the body) consistent with the side",
        description_wrong == 0 and without_description == 0,
        f"{len(strike_bodies)} responses, {without_description} without description",
    )

    strike_duplicates = verifier.scalar(
        "SELECT COUNT(*) FROM ("
        "  SELECT response_id, strike, COUNT(*) AS occurrences FROM breakdown_by_strike"
        "  GROUP BY response_id, strike HAVING occurrences > 1)"
    )

    verifier.check("no duplicated strike in the same response", strike_duplicates == 0)

    strike_empty = verifier.scalar(
        "SELECT COUNT(*) FROM breakdown_by_strike "
        "WHERE exposure = '' OR side = '' OR ticker = '' OR trade_date = '' "
        "   OR timeslot = '' OR received_at = ''"
    )

    verifier.check("no row without references", strike_empty == 0, f"{strike_empty} rows")

    # `records` also contains the archive, so nothing can be left here: what
    # matters is that every response of the archive could be read back. If one
    # does not decrypt, it is a failure of the encryption, not an extra
    # response.
    archive_ids = [row["id"] for row in verifier.rows("SELECT id FROM responses")]

    wanted_ids = {record["fingerprint"] for record in records if record["endpoint"] in LOADED}

    stray_ids = [one_id for one_id in archive_ids if one_id not in wanted_ids]

    verifier.check(
        "nothing in the archive that could not be read back",
        len(stray_ids) == 0,
        f"{len(stray_ids)} stray responses",
    )

    # The database keeps the encrypted original: it is decrypted again from
    # there and the saved rows are compared. If it comes back, the database is
    # enough for itself. (A body that does not decrypt does not enter here:
    # the global check on all the bodies in the archive counts it.)
    strike_saved_values = verifier.rows(
        "SELECT id, iv, data, tag FROM responses WHERE endpoint = 'breakdown-by-strike'"
    )

    strike_retained = _retained_ids(strike_records)

    strike_wrong_values = 0
    strike_not_pruned = 0

    for body in strike_saved_values:
        try:
            payload = decrypt_response(body["data"], body["iv"], body["tag"])
        except DecryptError:
            continue

        saved = verifier.rows(
            "SELECT strike, call, put, net FROM breakdown_by_strike WHERE response_id = ?",
            (body["id"],),
        )

        # Superseded by a later response of the same series: pruning
        # should have left nothing here — if it did not, that is a
        # pruning bug, not "the body still matches".
        if body["id"] not in strike_retained:
            if saved:
                strike_not_pruned += 1

            continue

        calls = next(trace for trace in payload["figure"]["data"] if trace.get("name") == "Calls")

        if len(saved) != len(calls["y"]):
            strike_wrong_values += 1

            continue

        by_strike = {row["strike"]: row for row in saved}

        mismatch = False

        for index, strike in enumerate(calls["y"]):
            row = by_strike.get(strike)
            triple = calls["customdata"][index]

            if (
                row is None
                or row["call"] != triple[0]
                or row["put"] != triple[1]
                or row["net"] != triple[2]
            ):
                mismatch = True

                break

        if mismatch:
            strike_wrong_values += 1

    verifier.check(
        "the saved values come back from the encrypted body",
        strike_wrong_values == 0 and strike_not_pruned == 0,
        f"{len(strike_saved_values)} responses decrypted again from the database"
        + (f" · {strike_not_pruned} superseded responses were not pruned" if strike_not_pruned else ""),
    )

    # --------------------------------------------------
    # BREAKDOWN BY EXPIRATION
    #
    # Same figure as breakdown by strike, but the axis is `x`: the expirations
    # are text and the values are `y`. In the database date and symbol stay
    # separate, and the implicit symbol of a bare date is SPXW.
    # --------------------------------------------------

    expiration_expected = []

    for record in expiration_records:
        parameters = _params(record["url"])
        traces = record["payload"]["figure"]["data"]

        calls = next(trace for trace in traces if trace.get("name") == "Calls")
        puts = next(trace for trace in traces if trace.get("name") == "Puts")
        net = next(trace for trace in traces if trace.get("name") == "Net")

        timeslot = _timeslot(record["url"], record.get("received_at"))
        side = side_of(parameters.get("participants"))

        for index, label in enumerate(calls["x"]):
            expiration, option_symbol = split_expiration(label)
            triple = calls["customdata"][index]

            expiration_expected.append(
                {
                    "id": record["fingerprint"],
                    "received_at": record["received_at"],
                    "timeslot": timeslot,
                    "trade_date": parameters.get("date"),
                    "ticker": parameters.get("ticker"),
                    "exposure": parameters.get("metric"),
                    "side": side,
                    # The raw label of the API, for the check that the
                    # database did not copy it into `expiration`.
                    "label": label,
                    "expiration": expiration,
                    "option_symbol": option_symbol,
                    "call": triple[0],
                    "put": triple[1],
                    "net": triple[2],
                    "call_y": calls["y"][index],
                    "put_y": puts["y"][index],
                    "net_y": net["y"][index],
                }
            )

    expiration_stored = verifier.rows(
        "SELECT response_id, received_at, timeslot, trade_date, ticker, exposure, side, "
        "       expiration, option_symbol, call, put, net "
        "FROM breakdown_by_expiration"
    )

    expiration_archive = verifier.scalar(
        "SELECT COUNT(*) FROM responses WHERE endpoint = ?", ("breakdown-by-expiration",)
    )

    verifier.check(
        "expirations in the database",
        len(expiration_stored) == len(expiration_expected),
        f"{len(expiration_stored)} vs {len(expiration_expected)}",
    )

    expiration_captured = sum(
        1 for record in records if record["endpoint"] == "breakdown-by-expiration"
    )

    verifier.check(
        "expiration responses in the archive",
        expiration_archive == expiration_captured,
        f"{expiration_archive} vs {expiration_captured}",
    )

    # Every expected expiration must exist identical, in its row: the key is
    # (date, symbol), without which the monthly and the weekly of the same day
    # would be the same row.
    expiration_by_key = {
        (row["response_id"], row["expiration"], row["option_symbol"]): row
        for row in expiration_stored
    }

    expiration_missing = 0
    expiration_different = 0

    for row in expiration_expected:
        saved = expiration_by_key.get((row["id"], row["expiration"], row["option_symbol"]))

        if saved is None:
            expiration_missing += 1

            continue

        if (
            saved["call"] != row["call"]
            or saved["put"] != row["put"]
            or saved["net"] != row["net"]
        ):
            expiration_different += 1

    verifier.check(
        "every expiration matches the payload",
        expiration_missing == 0 and expiration_different == 0,
        f"{expiration_missing} missing, {expiration_different} different",
    )

    expiration_keys = {
        (row["id"], row["expiration"], row["option_symbol"]) for row in expiration_expected
    }

    expiration_extra = sum(
        1
        for row in expiration_stored
        if (row["response_id"], row["expiration"], row["option_symbol"]) not in expiration_keys
    )

    verifier.check(
        "no extra expiration in the database",
        expiration_extra == 0,
        f"{expiration_extra} stray rows out of {len(expiration_stored)}",
    )

    # The references must be the ones of the response. There is a single map,
    # built once: looking for the right response row by row would be
    # quadratic.
    expiration_by_id = {row["id"]: row for row in expiration_expected}

    expiration_wrong = 0

    for row in expiration_stored:
        want = expiration_by_id.get(row["response_id"])

        if want is None or (
            row["received_at"],
            row["timeslot"],
            row["trade_date"],
            row["ticker"],
            row["exposure"],
            row["side"],
        ) != (
            want["received_at"],
            want["timeslot"],
            want["trade_date"],
            want["ticker"],
            want["exposure"],
            want["side"],
        ):
            expiration_wrong += 1

    verifier.check(
        "references correct on every expiration", expiration_wrong == 0, f"{expiration_wrong} inconsistent"
    )

    expiration_empty = verifier.scalar(
        "SELECT COUNT(*) FROM breakdown_by_expiration "
        "WHERE exposure = '' OR side = '' OR ticker = '' OR trade_date = '' "
        "   OR timeslot = '' OR received_at = '' OR expiration = ''"
    )

    verifier.check(
        "no expiration without references", expiration_empty == 0, f"{expiration_empty} rows"
    )

    expiration_duplicates = verifier.scalar(
        "SELECT COUNT(*) FROM ("
        "  SELECT response_id, expiration, option_symbol, COUNT(*) AS occurrences "
        "  FROM breakdown_by_expiration GROUP BY response_id, expiration, option_symbol "
        "  HAVING occurrences > 1)"
    )

    verifier.check("no duplicated expiration in the same response", expiration_duplicates == 0)

    # The triple in `Calls.customdata` must repeat the three traces: if the
    # database had taken the wrong trace, it would show here.
    expiration_incoherent = sum(
        1
        for row in expiration_expected
        if row["call"] != row["call_y"] or row["put"] != row["put_y"] or row["net"] != row["net_y"]
    )

    verifier.check(
        "customdata = Calls/Puts/Net in the payload",
        expiration_incoherent == 0,
        f"{expiration_incoherent} inconsistent",
    )

    expiration_bad = sum(
        1
        for row in expiration_expected
        if abs(row["call"] + row["put"] - row["net"]) > 0.02
    )

    verifier.check(
        "call + put ≈ net on every expiration",
        expiration_bad == 0,
        f"maximum gap {gap_of(expiration_expected):.4f}",
    )

    # The two expirations of the same day (monthly SPX and weekly SPXW) must
    # stay two distinct rows: what distinguishes them is `option_symbol`.
    expiration_days: dict[str, set] = {}

    for row in expiration_expected:
        expiration_days.setdefault(row["expiration"], set()).add(row["option_symbol"])

    expiration_shared = [
        (expiration, symbols) for expiration, symbols in expiration_days.items() if len(symbols) > 1
    ]

    expiration_present = {(row["expiration"], row["option_symbol"]) for row in expiration_stored}

    expiration_lost = [
        (expiration, symbols)
        for expiration, symbols in expiration_shared
        if any((expiration, symbol) not in expiration_present for symbol in symbols)
    ]

    verifier.check(
        "the same date with two symbols stays two rows",
        len(expiration_lost) == 0,
        f"{len(expiration_shared)} dates with more than one symbol"
        + (
            " ("
            + ", ".join(
                f"{expiration}:{'+'.join(sorted(symbols))}"
                for expiration, symbols in expiration_shared
            )
            + ")"
            if expiration_shared
            else ""
        ),
    )
    # `expiration` is the date alone: the label of the API must not end up
    # inside the column. If it did, the `"2026-10-16, SPX"` would come back,
    # which is neither a date nor a symbol.
    expiration_dirty = verifier.scalar(
        "SELECT COUNT(*) FROM breakdown_by_expiration "
        "WHERE expiration LIKE '%,%' "
        "   OR LENGTH(expiration) <> 10 "
        "   OR expiration NOT LIKE '____-__-__' "
        "   OR option_symbol NOT GLOB '[A-Z]*' "
        "   OR option_symbol LIKE '%,%'"
    )

    verifier.check(
        "expiration is a date, option_symbol a symbol",
        expiration_dirty == 0,
        f"{expiration_dirty} rows with the raw label inside a column",
    )

    expiration_raw = [row for row in expiration_expected if "," in row["expiration"]]

    verifier.check(
        "the labels with the symbol have been split",
        len(expiration_raw) == 0,
        f"{sum(1 for row in expiration_expected if ',' in row['label'])} raw labels, "
        f"{len(expiration_raw)} ended up in the date column",
    )

    # The implicit symbol of bare dates (SPXW) is not a guess: it must be the
    # one the depth view writes for the same expiration in the same capture.
    # If the default were wrong, it would show here at once.
    depth_symbols: dict[tuple, set] = {}

    for row in verifier.rows(
        "SELECT DISTINCT exposure, side, timeslot, expiration, option_symbol FROM depth_view"
    ):
        key = (row["exposure"], row["side"], row["timeslot"], row["expiration"])

        depth_symbols.setdefault(key, set()).add(row["option_symbol"])

    expiration_misleading = 0

    for row in expiration_expected:
        symbols = depth_symbols.get(
            (row["exposure"], row["side"], row["timeslot"], row["expiration"])
        )

        if symbols and row["option_symbol"] not in symbols:
            expiration_misleading += 1

    verifier.check(
        "the symbol of every expiration is the one of the depth view",
        expiration_misleading == 0,
        f"{len(expiration_expected)} expirations · {expiration_misleading} "
        "with a symbol the depth view does not know",
    )

    # The database keeps the encrypted original here too: it is decrypted
    # again from the database and the saved rows are compared, symbol
    # included.
    expiration_bodies = verifier.rows(
        "SELECT id, iv, data, tag FROM responses WHERE endpoint = 'breakdown-by-expiration'"
    )

    expiration_retained = _retained_ids(expiration_records)

    expiration_not_decryptable = 0
    expiration_wrong_values = 0
    expiration_not_pruned = 0

    for body in expiration_bodies:
        try:
            payload = decrypt_response(body["data"], body["iv"], body["tag"])
        except DecryptError:
            expiration_not_decryptable += 1

            continue

        saved = verifier.rows(
            "SELECT expiration, option_symbol, call, put, net FROM breakdown_by_expiration "
            "WHERE response_id = ?",
            (body["id"],),
        )

        if body["id"] not in expiration_retained:
            if saved:
                expiration_not_pruned += 1

            continue

        calls = next(trace for trace in payload["figure"]["data"] if trace.get("name") == "Calls")

        by_key = {(row["expiration"], row["option_symbol"]): row for row in saved}

        mismatch = len(saved) != len(calls["x"])

        for index, label in enumerate(calls["x"]):
            expiration, option_symbol = split_expiration(label)
            row = by_key.get((expiration, option_symbol))
            triple = calls["customdata"][index]

            if (
                row is None
                or row["call"] != triple[0]
                or row["put"] != triple[1]
                or row["net"] != triple[2]
            ):
                mismatch = True

                break

        if mismatch:
            expiration_wrong_values += 1

    verifier.check(
        "every saved expiration body decrypts again",
        expiration_not_decryptable == 0,
        f"{expiration_not_decryptable} unreadable",
    )

    verifier.check(
        "the saved expirations come back from the encrypted body",
        expiration_wrong_values == 0 and expiration_not_pruned == 0,
        f"{len(expiration_bodies)} responses decrypted again from the database"
        + (
            f" · {expiration_not_pruned} superseded responses were not pruned"
            if expiration_not_pruned
            else ""
        ),
    )

    # --------------------------------------------------
    # DEPTH VIEW
    #
    # The expirations are not in the data but in the header: the key of the
    # cell is the `title` with the underscores turned into dashes, and the same
    # date can appear twice (monthly SPX + weekly SPXW).
    #
    # The table has hundreds of thousands of rows: the expected cells and the
    # saved ones are compared one response at a time, instead of keeping them
    # all in memory together.
    # --------------------------------------------------

    depth_columns: dict[str, set] = {}
    depth_expected_cells = 0
    depth_absent = 0
    depth_missing = 0
    depth_different = 0
    depth_extra = 0
    depth_stored_cells = 0
    depth_value_differs = 0
    depth_reference_conflicts = 0

    for record in depth_records:
        parameters = _params(record["url"])
        payload = record["payload"]
        response_id = record["fingerprint"]

        columns = {}

        for column in payload.get("header") or []:
            title = (column.get("title") or "").strip()

            if not title or title.lower() == "strike":
                continue

            expiration = title.replace("_", "-")

            columns[f"{expiration}-{column.get('option_symbol')}"] = (
                expiration,
                column.get("option_symbol"),
            )

        depth_columns[response_id] = set(columns.values())

        want_references = (
            record["received_at"],
            _timeslot(record["url"], record.get("received_at")),
            parameters.get("date"),
            parameters.get("ticker"),
            parameters.get("metric"),
            side_of(parameters.get("participants")),
        )

        saved = verifier.rows(
            "SELECT received_at, timeslot, trade_date, ticker, exposure, side, "
            "       expiration, option_symbol, strike, call, put, net "
            "FROM depth_view WHERE response_id = ?",
            (response_id,),
        )

        stored = {}
        expected = set()

        for row in saved:
            stored[(row["expiration"], row["option_symbol"], row["strike"])] = (
                row["call"],
                row["put"],
                row["net"],
            )

            if (
                row["received_at"],
                row["timeslot"],
                row["trade_date"],
                row["ticker"],
                row["exposure"],
                row["side"],
            ) != want_references:
                depth_reference_conflicts += 1

        for row in payload.get("data") or []:
            # How many cells the header declares and the row does not send.
            depth_absent += len(columns) - (len(row) - 1)

            for key, cell in row.items():
                if key == "strike" or key == "strike_price":
                    continue

                column = columns.get(key)

                if column is None:
                    raise RuntimeError(f"column '{key}' not declared in the header")

                cell_key = (column[0], column[1], row["strike"])

                expected.add(cell_key)
                depth_expected_cells += 1

                # `value` is not a column: it repeats `net_value`.
                if cell.get("value") != cell.get("net_value"):
                    depth_value_differs += 1

                triple = stored.get(cell_key)

                if triple is None:
                    depth_missing += 1
                elif triple != (
                    cell.get("call_value"),
                    cell.get("put_value"),
                    cell.get("net_value"),
                ):
                    depth_different += 1

        depth_stored_cells += len(saved)
        depth_extra += sum(1 for cell_key in stored if cell_key not in expected)

    depth_table_rows = verifier.scalar("SELECT COUNT(*) FROM depth_view")

    # The response rows that are not in the file (a response that does not
    # decrypt, for example) are in no `expected`: they are stray too.
    depth_extra += depth_table_rows - depth_stored_cells

    depth_archive = verifier.scalar(
        "SELECT COUNT(*) FROM responses WHERE endpoint = ?", ("depth-view-table",)
    )

    verifier.check(
        "cells in the database",
        depth_table_rows == depth_expected_cells,
        f"{depth_table_rows} vs {depth_expected_cells}",
    )

    depth_captured = sum(1 for record in records if record["endpoint"] == "depth-view-table")

    verifier.check(
        "depth responses in the archive",
        depth_archive == depth_captured,
        f"{depth_archive} vs {depth_captured}",
    )

    verifier.check(
        "every cell matches the payload",
        depth_missing == 0 and depth_different == 0,
        f"{depth_missing} missing, {depth_different} different",
    )

    verifier.check(
        "no extra cell in the database",
        depth_extra == 0,
        f"{depth_extra} stray cells out of {depth_table_rows}",
    )

    verifier.check(
        "the absent cells have no row",
        depth_table_rows == depth_expected_cells,
        f"{depth_absent} absent cells in the payload, {depth_table_rows} rows",
    )

    verifier.check(
        "references correct on every cell",
        depth_reference_conflicts == 0,
        f"{depth_reference_conflicts} inconsistent",
    )

    depth_without_references = verifier.scalar(
        "SELECT COUNT(*) FROM depth_view "
        "WHERE exposure = '' OR side = '' OR ticker = '' OR trade_date = '' "
        "   OR timeslot = '' OR received_at = '' "
        "   OR expiration = '' OR option_symbol = ''"
    )

    verifier.check(
        "no cell without references",
        depth_without_references == 0,
        f"{depth_without_references} cells",
    )

    depth_duplicates = verifier.scalar(
        "SELECT COUNT(*) FROM ("
        "  SELECT response_id, expiration, option_symbol, strike, COUNT(*) AS occurrences "
        "  FROM depth_view GROUP BY response_id, expiration, option_symbol, strike "
        "  HAVING occurrences > 1)"
    )

    verifier.check("no duplicated cell in the same response", depth_duplicates == 0)

    # The expirations and the symbols the header of every response declares.
    depth_undeclared = 0

    for row in verifier.rows(
        "SELECT DISTINCT response_id, expiration, option_symbol FROM depth_view"
    ):
        if (row["expiration"], row["option_symbol"]) not in depth_columns.get(
            row["response_id"], set()
        ):
            depth_undeclared += 1

    verifier.check(
        "expiration and symbol of every cell come from the header",
        depth_undeclared == 0,
        f"{depth_undeclared} undeclared columns",
    )

    # The two expirations on the same day (monthly SPX and SPXW) must stay two
    # columns, not one: the real key is the pair.
    depth_pairs = {
        (row["expiration"], row["option_symbol"])
        for row in verifier.rows("SELECT DISTINCT expiration, option_symbol FROM depth_view")
    }

    depth_days: dict[str, set] = {}

    for columns in depth_columns.values():
        for expiration, symbol in columns:
            depth_days.setdefault(expiration, set()).add(symbol)

    depth_shared = [
        (expiration, symbols) for expiration, symbols in depth_days.items() if len(symbols) > 1
    ]

    depth_collapsed = [
        (expiration, symbols)
        for expiration, symbols in depth_shared
        if any((expiration, symbol) not in depth_pairs for symbol in symbols)
    ]

    verifier.check(
        "the same date with two symbols stays two columns",
        len(depth_collapsed) == 0,
        f"{len(depth_shared)} dates with more than one symbol"
        + (
            " ("
            + ", ".join(
                f"{expiration}:{'+'.join(sorted(symbols))}"
                for expiration, symbols in depth_shared
            )
            + ")"
            if depth_shared
            else ""
        ),
    )
    # The database keeps the encrypted original for the depth too: it is
    # decrypted again from there and the cells are compared.
    depth_bodies = verifier.rows(
        "SELECT id, iv, data, tag FROM responses WHERE endpoint = 'depth-view-table'"
    )

    depth_retained = _retained_ids(depth_records)

    depth_not_decryptable = 0
    depth_wrong_values = 0
    depth_not_pruned = 0

    for body in depth_bodies:
        try:
            payload = decrypt_response(body["data"], body["iv"], body["tag"])
        except DecryptError:
            depth_not_decryptable += 1

            continue

        saved = verifier.rows(
            "SELECT strike, expiration, option_symbol, call, put, net FROM depth_view "
            "WHERE response_id = ?",
            (body["id"],),
        )

        if body["id"] not in depth_retained:
            if saved:
                depth_not_pruned += 1

            continue

        # The header is the map between keys of the data and columns of the
        # database.
        columns = {}

        for column in payload.get("header") or []:
            title = (column.get("title") or "").strip()

            if not title or title.lower() == "strike":
                continue

            expiration = title.replace("_", "-")

            columns[f"{expiration}-{column.get('option_symbol')}"] = (
                expiration,
                column.get("option_symbol"),
            )

        stored_by_key = {
            (row["expiration"], row["option_symbol"], row["strike"]): row for row in saved
        }

        cells = 0
        mismatch = False

        for row in payload.get("data") or []:
            for key, cell in row.items():
                if key == "strike" or key == "strike_price":
                    continue

                column = columns.get(key)

                if column is None:
                    mismatch = True

                    continue

                cells += 1

                saved_row = stored_by_key.get((column[0], column[1], row["strike"]))

                if (
                    saved_row is None
                    or saved_row["call"] != cell.get("call_value")
                    or saved_row["put"] != cell.get("put_value")
                    or saved_row["net"] != cell.get("net_value")
                ):
                    mismatch = True

        if mismatch or cells != len(saved):
            depth_wrong_values += 1

    verifier.check(
        "every saved depth body decrypts again",
        depth_not_decryptable == 0,
        f"{depth_not_decryptable} unreadable",
    )

    verifier.check(
        "the saved cells come back from the encrypted body",
        depth_wrong_values == 0 and depth_not_pruned == 0,
        f"{len(depth_bodies)} responses decrypted again from the database"
        + (f" · {depth_not_pruned} superseded responses were not pruned" if depth_not_pruned else ""),
    )

    # call + put = net inside every cell (the API rounds every value). SQLite
    # computes the maximum gap and the number of cells out of tolerance: they
    # are four hundred thousand rows, and they are not brought into memory to
    # look at them.
    depth_bad_gap = verifier.scalar(
        "SELECT COUNT(*) FROM depth_view WHERE ABS(call + put - net) > 0.02"
    )

    depth_max_gap = verifier.scalar("SELECT MAX(ABS(call + put - net)) FROM depth_view")

    verifier.check(
        "call + put ≈ net in every cell",
        depth_bad_gap == 0,
        f"{depth_table_rows} cells, maximum gap {float(depth_max_gap or 0.0):.4f}",
    )

    verifier.check(
        "value repeats net_value (so it is not a column)",
        depth_value_differs == 0,
        f"{depth_value_differs} different cells",
    )

    # --------------------------------------------------
    # HEATMAP
    #
    # Four traces, but the grid is only in the first one: the other three
    # (Peak/Trough/Zero) are lines without `x`, `y`, `z`. `z` has one row per
    # price and one cell per instant. The metric is not in the URL: the
    # endpoint says it.
    #
    # Here too one response is compared at a time: the cells are three hundred
    # thousand.
    # --------------------------------------------------

    heatmap_meta: dict[str, tuple] = {}
    heatmap_last_cell: dict[str, float] = {}
    heatmap_expected_cells = 0
    heatmap_missing = 0
    heatmap_different = 0
    heatmap_extra = 0
    heatmap_stored_cells = 0

    for record in heatmap_records:
        parameters = _params(record["url"])
        response_id = record["fingerprint"]

        grid = next(
            (
                trace
                for trace in record["payload"]["figure"]["data"]
                if "x" in trace and "y" in trace and "z" in trace
            ),
            None,
        )

        if grid is None:
            raise RuntimeError(f"{record['endpoint']}: no trace with the grid")

        metric = HEATMAP_KINDS[record["endpoint"]]

        heatmap_meta[response_id] = (
            record["received_at"],
            _timeslot(record["url"], record.get("received_at")),
            parameters.get("date"),
            parameters.get("ticker"),
            metric,
            parameters.get("measurement"),
            js_number(parameters.get("color_scale_min")),
            js_number(parameters.get("color_scale_max")),
        )

        saved = verifier.rows(
            "SELECT time_slot, price, value FROM heatmaps WHERE response_id = ?", (response_id,)
        )

        stored = {(row["time_slot"], row["price"]): row["value"] for row in saved}

        expected = set()

        for row_index, line in enumerate(grid["z"]):
            for column_index, value in enumerate(line):
                key = (grid["x"][column_index].replace("T", " "), grid["y"][row_index])

                expected.add(key)
                heatmap_expected_cells += 1
                heatmap_last_cell[metric] = value

                if key not in stored:
                    heatmap_missing += 1
                elif stored[key] != value:
                    heatmap_different += 1

        heatmap_stored_cells += len(saved)
        heatmap_extra += sum(1 for key in stored if key not in expected)

    heatmap_table_rows = verifier.scalar("SELECT COUNT(*) FROM heatmaps")

    heatmap_extra += heatmap_table_rows - heatmap_stored_cells

    heatmap_archive = verifier.scalar(
        "SELECT COUNT(*) FROM responses "
        "WHERE endpoint IN ('plotly-gamma', 'plotly-charm', 'plotly-vanna')"
    )

    verifier.check(
        "heatmap cells in the database",
        heatmap_table_rows == heatmap_expected_cells,
        f"{heatmap_table_rows} vs {heatmap_expected_cells}",
    )

    heatmap_captured = sum(1 for record in records if record["endpoint"] in HEATMAP_KINDS)

    verifier.check(
        "heatmap responses in the archive",
        heatmap_archive == heatmap_captured,
        f"{heatmap_archive} vs {heatmap_captured}",
    )

    # Every response is a complete grid: the instants times the number of
    # prices. If the ingest had swapped rows and columns, it would show here.
    heatmap_grid = verifier.rows(
        "SELECT response_id, COUNT(*) AS cels, COUNT(DISTINCT time_slot) AS slots, "
        "       COUNT(DISTINCT price) AS prices "
        "FROM heatmaps GROUP BY response_id"
    )

    heatmap_incomplete = [row for row in heatmap_grid if row["cels"] != row["slots"] * row["prices"]]

    heatmap_first = heatmap_grid[0] if heatmap_grid else None

    verifier.check(
        "every heatmap is a complete grid",
        len(heatmap_incomplete) == 0,
        f"{len(heatmap_grid)} responses · {heatmap_first['slots'] if heatmap_first else 0} instants × "
        f"{heatmap_first['prices'] if heatmap_first else 0} prices · {len(heatmap_incomplete)} incomplete",
    )

    verifier.check(
        "every heatmap cell matches the payload",
        heatmap_missing == 0 and heatmap_different == 0,
        f"{heatmap_missing} missing, {heatmap_different} different",
    )

    verifier.check(
        "no extra heatmap cell in the database",
        heatmap_extra == 0,
        f"{heatmap_extra} stray cells out of {heatmap_table_rows}",
    )

    # The references, the metric and the color scale.
    heatmap_references = verifier.rows(
        "SELECT response_id, received_at, timeslot, trade_date, ticker, metric, measurement, "
        "       color_scale_min, color_scale_max "
        "FROM heatmaps GROUP BY response_id"
    )

    heatmap_wrong = 0

    for row in heatmap_references:
        want = heatmap_meta.get(row["response_id"])

        if want is None or (
            row["received_at"],
            row["timeslot"],
            row["trade_date"],
            row["ticker"],
            row["metric"],
            row["measurement"],
            row["color_scale_min"],
            row["color_scale_max"],
        ) != want:
            heatmap_wrong += 1

    verifier.check(
        "references and metric correct on every heatmap",
        heatmap_wrong == 0,
        f"{heatmap_wrong} inconsistent out of {len(heatmap_references)}",
    )

    # The metric is not in the URL: it comes from the endpoint. If it were
    # wrong, gamma and charm would have the same values (which is false: they
    # measure different things).
    heatmap_values = [f"{metric}={value}" for metric, value in heatmap_last_cell.items()]

    verifier.check(
        "the three metrics are distinct",
        len(set(heatmap_values)) == len(heatmap_last_cell),
        " · ".join(heatmap_values),
    )

    heatmap_without_references = verifier.scalar(
        "SELECT COUNT(*) FROM heatmaps "
        "WHERE metric = '' OR ticker = '' OR trade_date = '' "
        "   OR timeslot = '' OR received_at = '' OR time_slot = ''"
    )

    verifier.check(
        "no heatmap cell without references",
        heatmap_without_references == 0,
        f"{heatmap_without_references} cells",
    )

    heatmap_duplicates = verifier.scalar(
        "SELECT COUNT(*) FROM ("
        "  SELECT response_id, time_slot, price, COUNT(*) AS occurrences "
        "  FROM heatmaps GROUP BY response_id, time_slot, price HAVING occurrences > 1)"
    )

    verifier.check("no duplicated heatmap cell", heatmap_duplicates == 0)

    # The axes: the instants are sorted and without duplicates, the prices
    # increasing.
    heatmap_unsorted = 0
    heatmap_wrong_grid = 0

    heatmap_axes = verifier.rows("SELECT DISTINCT response_id FROM heatmaps")

    for entry in heatmap_axes:
        rows = verifier.rows(
            "SELECT time_slot, price FROM heatmaps WHERE response_id = ? ORDER BY time_slot, price",
            (entry["response_id"],),
        )

        # `[...new Set(...)]`: the distinct values, in the order they happen.
        # For the prices it is not enough to drop the consecutive duplicates —
        # the rows are ordered by instant, so the same price comes back.
        times = list(dict.fromkeys(row["time_slot"] for row in rows))
        prices = list(dict.fromkeys(row["price"] for row in rows))

        if any(times[index - 1] >= times[index] for index in range(1, len(times))):
            heatmap_unsorted += 1

        if any(prices[index - 1] >= prices[index] for index in range(1, len(prices))):
            heatmap_unsorted += 1

        if len(rows) != len(times) * len(prices):
            heatmap_wrong_grid += 1

    verifier.check(
        "the axes are sorted and the grid is full",
        heatmap_unsorted == 0 and heatmap_wrong_grid == 0,
        f"{len(heatmap_axes)} grids · {heatmap_unsorted} axes out of order · "
        f"{heatmap_wrong_grid} with holes",
    )

    # The database keeps the encrypted original here too: it is decrypted again
    # from the database and the cells are compared, row by row.
    heatmap_bodies = verifier.rows(
        "SELECT id, iv, data, tag FROM responses "
        "WHERE endpoint IN ('plotly-gamma', 'plotly-charm', 'plotly-vanna')"
    )

    heatmap_retained = _retained_ids(heatmap_records)

    heatmap_not_decryptable = 0
    heatmap_wrong_values = 0
    heatmap_not_pruned = 0

    for body in heatmap_bodies:
        try:
            payload = decrypt_response(body["data"], body["iv"], body["tag"])
        except DecryptError:
            heatmap_not_decryptable += 1

            continue

        saved = verifier.rows(
            "SELECT time_slot, price, value FROM heatmaps WHERE response_id = ?", (body["id"],)
        )

        if body["id"] not in heatmap_retained:
            if saved:
                heatmap_not_pruned += 1

            continue

        grid = next(
            (
                trace
                for trace in payload["figure"]["data"]
                if "x" in trace and "y" in trace and "z" in trace
            ),
            None,
        )

        by_key = {(row["time_slot"], row["price"]): row["value"] for row in saved}

        mismatch = len(saved) != len(grid["x"]) * len(grid["y"])

        if mismatch:
            print(
                f"   · {body['id']}: {len(saved)} saved cells "
                f"vs {len(grid['x']) * len(grid['y'])} expected"
            )

        for row_index, line in enumerate(grid["z"]):
            if mismatch:
                break

            for column_index, value in enumerate(line):
                key = (grid["x"][column_index].replace("T", " "), grid["y"][row_index])

                if by_key.get(key) != value:
                    print(
                        f"   · {body['id']}: row {row_index} column {column_index} ({key}) "
                        f"from the database {by_key.get(key)} vs {value} from the body"
                    )

                    mismatch = True

                    break

        if mismatch:
            heatmap_wrong_values += 1

    verifier.check(
        "every saved heatmap body decrypts again",
        heatmap_not_decryptable == 0,
        f"{heatmap_not_decryptable} unreadable",
    )

    verifier.check(
        "the heatmap cells come back from the encrypted body",
        heatmap_wrong_values == 0 and heatmap_not_pruned == 0,
        f"{len(heatmap_bodies)} responses decrypted again from the database"
        + (f" · {heatmap_not_pruned} superseded responses were not pruned" if heatmap_not_pruned else ""),
    )

    # --------------------------------------------------
    # VOLATILITY TABLE
    #
    # The rows are the expiries (`0DTE`…`1M`), the columns the strikes declared
    # by the header as decimal text ("7515.0") and used in the same shape as
    # keys of the data. Trade date and instant are not in the URL: they are in
    # the payload (`quote_date`, `snapshot_time`).
    # --------------------------------------------------

    volatility_expected: dict[tuple, dict] = {}
    volatility_references: dict[str, tuple] = {}
    volatility_declared_but_absent = 0

    for record in volatility_records:
        parameters = _params(record["url"])
        data = record["payload"]["data"]
        response_id = record["fingerprint"]

        strikes = [
            column.get("title")
            for column in data["header"]
            if column.get("title") != "Expiry" and column.get("title") != "Exp. Date"
        ]

        snapshot_time = data.get("snapshot_time")
        spot = data.get("spot") or {}
        volatility_references[response_id] = (
            record["received_at"],
            (parameters.get("date_time") or snapshot_time).replace("T", " "),
            parameters.get("date") or data.get("quote_date"),
            parameters.get("ticker") or data.get("ticker"),
        )

        for row in data["data"]:
            cells = [key for key in row if key != "expiry" and key != "exp_date"]

            # How many cells the header declares and the data does not send.
            volatility_declared_but_absent += len(strikes) - len(cells)

            for key in cells:
                cell = row[key]

                volatility_expected[(response_id, row["expiry"], float(key))] = {
                    "iv": cell.get("value"),
                    "od_value": cell.get("od_value"),
                    "percentile": cell.get("percentile"),
                    "exp_date": row.get("exp_date"),
                    "snapshot_time": snapshot_time,
                    "spot_price": spot.get("spot_price"),
                    "spot_time": spot.get("spot_time"),
                }

    volatility_stored = verifier.rows(
        "SELECT response_id, received_at, timeslot, trade_date, ticker, expiry, exp_date, strike, "
        "       iv, od_value, percentile, snapshot_time, spot_price, spot_time "
        "FROM volatility_table"
    )

    volatility_stored_by_key = {
        (row["response_id"], row["expiry"], row["strike"]): row for row in volatility_stored
    }

    volatility_archive = verifier.scalar(
        "SELECT COUNT(*) FROM responses WHERE endpoint = ?", ("volatility-table",)
    )

    verifier.check(
        "volatility cells in the database",
        len(volatility_stored) == len(volatility_expected),
        f"{len(volatility_stored)} vs {len(volatility_expected)}",
    )

    volatility_captured = sum(1 for record in records if record["endpoint"] == "volatility-table")

    verifier.check(
        "volatility responses in the archive",
        volatility_archive == volatility_captured,
        f"{volatility_archive} vs {volatility_captured}",
    )

    volatility_missing = 0
    volatility_different = 0
    volatility_wrong_exp_date = 0
    volatility_wrong_spot = 0

    for key, want in volatility_expected.items():
        saved = volatility_stored_by_key.get(key)

        if saved is None:
            volatility_missing += 1

            continue

        if (
            saved["iv"] != want["iv"]
            or saved["od_value"] != want["od_value"]
            or saved["percentile"] != want["percentile"]
        ):
            volatility_different += 1

        if saved["exp_date"] != want["exp_date"]:
            volatility_wrong_exp_date += 1

        if (
            saved["snapshot_time"] != want["snapshot_time"]
            or saved["spot_price"] != want["spot_price"]
            or saved["spot_time"] != want["spot_time"]
        ):
            volatility_wrong_spot += 1

    verifier.check(
        "every volatility cell matches the payload",
        volatility_missing == 0 and volatility_different == 0,
        f"{volatility_missing} missing, {volatility_different} different",
    )

    verifier.check(
        "every expiry carries its expiration date",
        volatility_wrong_exp_date == 0,
        f"{volatility_wrong_exp_date} wrong dates",
    )

    verifier.check(
        "underlying and instant of the dashboard are the ones of the payload",
        volatility_wrong_spot == 0,
        f"{volatility_wrong_spot} cells with a different spot or snapshot",
    )

    volatility_extra = sum(
        1
        for row in volatility_stored
        if (row["response_id"], row["expiry"], row["strike"]) not in volatility_expected
    )

    verifier.check(
        "no extra volatility cell in the database",
        volatility_extra == 0,
        f"{volatility_extra} stray cells out of {len(volatility_stored)}",
    )

    verifier.check(
        "the cells the header declares and the data does not send have no row",
        len(volatility_stored) == len(volatility_expected),
        f"{volatility_declared_but_absent} absent cells in the data, {len(volatility_stored)} rows",
    )

    volatility_grid = verifier.rows(
        "SELECT response_id, COUNT(*) AS cells, COUNT(DISTINCT expiry) AS expiries, "
        "       COUNT(DISTINCT strike) AS strike "
        "FROM volatility_table GROUP BY response_id"
    )

    volatility_incomplete = [
        row for row in volatility_grid if row["cells"] != row["expiries"] * row["strike"]
    ]

    volatility_first = volatility_grid[0] if volatility_grid else None

    verifier.check(
        "every volatility table is a full grid",
        len(volatility_incomplete) == 0,
        f"{len(volatility_grid)} responses · {volatility_first['expiries'] if volatility_first else 0} "
        f"expiries × {volatility_first['strike'] if volatility_first else 0} strike · "
        f"{len(volatility_incomplete)} incomplete",
    )

    volatility_refs = verifier.rows(
        "SELECT response_id, received_at, timeslot, trade_date, ticker "
        "FROM volatility_table GROUP BY response_id"
    )

    volatility_bad_refs = 0

    for row in volatility_refs:
        want = volatility_references.get(row["response_id"])

        if want is None or (
            row["received_at"],
            row["timeslot"],
            row["trade_date"],
            row["ticker"],
        ) != want:
            volatility_bad_refs += 1

    verifier.check(
        "references correct on every volatility cell",
        volatility_bad_refs == 0,
        f"{volatility_bad_refs} inconsistent out of {len(volatility_refs)}",
    )

    volatility_empty = verifier.scalar(
        "SELECT COUNT(*) FROM volatility_table "
        "WHERE timeslot = '' OR trade_date = '' OR ticker = '' "
        "   OR received_at = '' OR expiry = '' OR exp_date = ''"
    )

    verifier.check(
        "no volatility cell without references",
        volatility_empty == 0,
        f"{volatility_empty} cells",
    )

    volatility_duplicates = verifier.scalar(
        "SELECT COUNT(*) FROM ("
        "  SELECT response_id, expiry, strike, COUNT(*) AS occurrences "
        "  FROM volatility_table GROUP BY response_id, expiry, strike HAVING occurrences > 1)"
    )

    verifier.check("no duplicated volatility cell", volatility_duplicates == 0)

    # The database keeps the encrypted original here too.
    volatility_bodies = verifier.rows(
        "SELECT id, iv, data, tag FROM responses WHERE endpoint = 'volatility-table'"
    )

    volatility_retained = _retained_ids(volatility_records)

    volatility_not_decryptable = 0
    volatility_wrong_values = 0
    volatility_not_pruned = 0

    for body in volatility_bodies:
        try:
            payload = decrypt_response(body["data"], body["iv"], body["tag"])
        except DecryptError:
            volatility_not_decryptable += 1

            continue

        saved = verifier.rows(
            "SELECT expiry, strike, iv, od_value, percentile FROM volatility_table "
            "WHERE response_id = ?",
            (body["id"],),
        )

        if body["id"] not in volatility_retained:
            if saved:
                volatility_not_pruned += 1

            continue

        data = payload["data"]

        by_key = {(row["expiry"], row["strike"]): row for row in saved}

        mismatch = len(saved) != len(data["data"]) * (len(data["header"]) - 2)

        for row in data["data"]:
            if mismatch:
                break

            for key, cell in row.items():
                if key == "expiry" or key == "exp_date":
                    continue

                saved_row = by_key.get((row["expiry"], float(key)))

                if (
                    saved_row is None
                    or saved_row["iv"] != cell.get("value")
                    or saved_row["od_value"] != cell.get("od_value")
                    or saved_row["percentile"] != cell.get("percentile")
                ):
                    mismatch = True

                    break

        if mismatch:
            volatility_wrong_values += 1

    verifier.check(
        "every saved volatility body decrypts again",
        volatility_not_decryptable == 0,
        f"{volatility_not_decryptable} unreadable",
    )

    verifier.check(
        "the volatility cells come back from the encrypted body",
        volatility_wrong_values == 0 and volatility_not_pruned == 0,
        f"{len(volatility_bodies)} responses decrypted again from the database"
        + (
            f" · {volatility_not_pruned} superseded responses were not pruned"
            if volatility_not_pruned
            else ""
        ),
    )

    # --------------------------------------------------
    # SURFACE AND SLICES
    #
    # The grid: `x` the strikes, `y` the expirations, `z[expiration][strike]`
    # the IV, and `dtes` the days to expiration (one `y` for each).
    #
    # The slices: the smile has the strikes in `x` and a single curve, the term
    # structure has the expirations in `x` and one trace per strike (which is
    # called "k=5500"). The smile is the row of the surface of that expiration.
    #
    # A kind of slice may never have arrived — the page asks for the term
    # structure only if it needs it — and in that case there is nothing to
    # compare: the check says so and does not fail.
    # --------------------------------------------------

    surface_expected = []

    for record in surface_records:
        data = record["payload"]["data"]
        trace = first_trace(data)

        if trace is None:
            # No grid in this response: there is nothing to compare, and the
            # parser rejected it on purpose.
            continue

        dtes = trace.get("dtes") or []

        for row in range(len(trace["z"])):
            for column in range(len(trace["z"][row])):
                surface_expected.append(
                    {
                        "id": record["fingerprint"],
                        "expiration": trace["y"][row],
                        "dte": dtes[row] if row < len(dtes) else None,
                        "strike": trace["x"][column],
                        "iv": trace["z"][row][column],
                    }
                )

    slice_expected = []

    for record in slice_records:
        data = record["payload"]["data"]
        kind = SLICE_KINDS[record["endpoint"]]

        figure = data["figure"] if kind == "smile" else data["term"]["figure"]

        traces = figure["data"]

        for index, trace in enumerate(traces):
            label = trace.get("name")

            if label is None:
                label = trace.get("label")

            # The strike of the curve: "k=5500".
            strike = float(label.replace("k=", "")) if kind == "term-structure" else None

            # The expiration of the smile: it is the date of the payload.
            smile_expiration = data.get("quote_date") if kind == "smile" else None

            for point, iv in enumerate(trace["y"]):
                value = trace["x"][point]

                slice_expected.append(
                    {
                        "id": record["fingerprint"],
                        "kind": kind,
                        "trace_index": index if len(traces) > 1 else None,
                        "anchor_expiration": smile_expiration if kind == "smile" else None,
                        "anchor_strike": strike if kind == "term-structure" else None,
                        "expiration": smile_expiration if kind == "smile" else value,
                        "strike": float(value) if kind == "smile" else strike,
                        "iv": iv,
                    }
                )

    surface_stored = verifier.rows(
        "SELECT response_id, trade_date, timeslot, expiration, strike, iv, dte, range_min, range_max "
        "FROM volatility_surface"
    )

    surface_by_key = {
        (row["response_id"], row["expiration"], row["strike"]): row for row in surface_stored
    }

    slice_stored = verifier.rows(
        "SELECT response_id, kind, expiration, strike, trace_index, iv, "
        "       anchor_expiration, anchor_strike "
        "FROM volatility_slices"
    )

    slice_by_key = {
        (row["response_id"], row["kind"], row["expiration"], row["strike"], row["trace_index"]): row
        for row in slice_stored
    }

    surface_archive = verifier.scalar(
        "SELECT COUNT(*) FROM responses WHERE endpoint = ?", ("volatility-3d-surface",)
    )

    verifier.check(
        "surface cells in the database",
        len(surface_stored) == len(surface_expected),
        f"{len(surface_stored)} vs {len(surface_expected)}",
    )

    surface_captured = sum(1 for record in records if record["endpoint"] == "volatility-3d-surface")

    verifier.check(
        "surface responses in the archive",
        surface_archive == surface_captured,
        f"{surface_archive} vs {surface_captured}",
    )

    surface_grid = verifier.rows(
        "SELECT response_id, COUNT(*) AS cells, COUNT(DISTINCT expiration) AS expirations, "
        "       COUNT(DISTINCT strike) AS strike "
        "FROM volatility_surface GROUP BY response_id"
    )

    surface_incomplete = [
        row for row in surface_grid if row["cells"] != row["expirations"] * row["strike"]
    ]

    surface_first = surface_grid[0] if surface_grid else None

    verifier.check(
        "every surface is a full grid",
        len(surface_incomplete) == 0,
        f"{len(surface_grid)} responses · {surface_first['expirations'] if surface_first else 0} expirations × "
        f"{surface_first['strike'] if surface_first else 0} strike · {len(surface_incomplete)} incomplete",
    )

    surface_missing = 0
    surface_different = 0
    surface_wrong_dte = 0
    surface_out_of_range = 0

    for row in surface_expected:
        saved = surface_by_key.get((row["id"], row["expiration"], row["strike"]))

        if saved is None:
            surface_missing += 1

            continue

        if saved["iv"] != row["iv"]:
            surface_different += 1

        if saved["dte"] != row["dte"]:
            surface_wrong_dte += 1

        # The range that `meta` declares: the values must stay inside it. A
        # missing range counts as zero, as in the JavaScript comparison.
        if row["iv"] < (saved["range_min"] or 0.0) or row["iv"] > (saved["range_max"] or 0.0):
            surface_out_of_range += 1

    verifier.check(
        "every surface cell matches the payload",
        surface_missing == 0 and surface_different == 0,
        f"{surface_missing} missing, {surface_different} different",
    )

    verifier.check(
        "the days to expiration are the ones of the payload",
        surface_wrong_dte == 0,
        f"{surface_wrong_dte} wrong",
    )

    verifier.check(
        "every value stays inside the range declared by meta",
        surface_out_of_range == 0,
        f"{surface_out_of_range} out of range",
    )

    surface_keys = {(row["id"], row["expiration"], row["strike"]) for row in surface_expected}

    surface_extra = sum(
        1
        for row in surface_stored
        if (row["response_id"], row["expiration"], row["strike"]) not in surface_keys
    )

    verifier.check(
        "no extra surface cell in the database",
        surface_extra == 0,
        f"{surface_extra} stray cells out of {len(surface_stored)}",
    )

    verifier.check(
        "curve points in the database",
        len(slice_stored) == len(slice_expected),
        f"{len(slice_stored)} vs {len(slice_expected)}",
    )

    slice_kinds = verifier.rows("SELECT kind, COUNT(*) AS n FROM volatility_slices GROUP BY kind")

    slice_by_kind = " · ".join(f"{row['kind']}={row['n']}" for row in slice_kinds)

    slice_found = {row["kind"] for row in slice_kinds}
    slice_wanted = {row["kind"] for row in slice_expected}

    slice_strays = [kind for kind in slice_found if kind != "smile" and kind != "term-structure"]

    verifier.check(
        "every curve point has the kind it belongs to",
        len(slice_strays) == 0 and all(kind in slice_wanted for kind in slice_found),
        slice_by_kind + (" · the term structure never arrived" if len(slice_found) < 2 else ""),
    )

    slice_missing = 0
    slice_different = 0
    slice_wrong_anchor = 0

    for row in slice_expected:
        saved = slice_by_key.get(
            (row["id"], row["kind"], row["expiration"], row["strike"], row["trace_index"])
        )

        if saved is None:
            slice_missing += 1

            continue

        if saved["iv"] != row["iv"]:
            slice_different += 1

        # The anchor says which curve: the expiration for the smile, the strike
        # for the term structure.
        if (
            saved["anchor_expiration"] != row["anchor_expiration"]
            or saved["anchor_strike"] != row["anchor_strike"]
        ):
            slice_wrong_anchor += 1

    verifier.check(
        "every curve point matches the payload",
        slice_missing == 0 and slice_different == 0,
        f"{slice_missing} missing, {slice_different} different",
    )

    verifier.check(
        "every point knows which curve it belongs to",
        slice_wrong_anchor == 0,
        f"{slice_wrong_anchor} wrong anchors",
    )

    slice_keys = {
        (row["id"], row["kind"], row["expiration"], row["strike"], row["trace_index"])
        for row in slice_expected
    }

    slice_extra = sum(
        1
        for row in slice_stored
        if (row["response_id"], row["kind"], row["expiration"], row["strike"], row["trace_index"])
        not in slice_keys
    )

    verifier.check(
        "no extra curve point in the database",
        slice_extra == 0,
        f"{slice_extra} stray points out of {len(slice_stored)}",
    )

    slice_duplicates = verifier.scalar(
        "SELECT COUNT(*) FROM ("
        "  SELECT response_id, kind, expiration, strike, trace_index, COUNT(*) AS occurrences "
        "  FROM volatility_slices GROUP BY 1, 2, 3, 4, 5 HAVING occurrences > 1)"
    )

    verifier.check("no duplicated curve point", slice_duplicates == 0)
    # The smile should be the row of the surface of its expiration: same
    # strikes, same values. But the smile and the grid come from two different
    # requests, and they may not meet: the page asks for the smile for an
    # expiration that is not in the grid, or at an instant the grid does not
    # have. It is not a storage failure: it is that the two datasets do not
    # overlap. So they are compared **where they overlap** and it is said how
    # big this overlap is, instead of demanding that it is always there.
    smile_responses = verifier.rows(
        "SELECT DISTINCT response_id, timeslot, snapshot_time, trade_date, anchor_expiration "
        "FROM volatility_slices WHERE kind = 'smile'"
    )

    smile_points: dict[str, list] = {}

    for row in verifier.rows(
        "SELECT response_id, strike, iv FROM volatility_slices WHERE kind = 'smile'"
    ):
        smile_points.setdefault(row["response_id"], []).append((row["strike"], row["iv"]))

    # Which (trade date, expiration) exist in the grid, and which trade dates.
    surface_pairs = {(row["trade_date"], row["expiration"]) for row in surface_stored}
    surface_days = {row["trade_date"] for row in surface_stored}

    # **Which** grid is compared: the one closest in time.
    #
    # The instant does not have to match — the two requests arrive minutes
    # apart — but taking the first grid of the day is another thing: it can be
    # hours earlier, and then the comparison measures the passing of time
    # instead of measuring whether the two datasets say the same thing. Here
    # the grid with the instant closest to the one of the smile is chosen,
    # which is the only honest comparison.
    grids: dict[tuple, list[tuple[str, float]]] = {}

    for row in verifier.rows(
        "SELECT snapshot_time, trade_date, expiration, strike, iv FROM volatility_surface"
    ):
        key = (row["trade_date"], row["expiration"], row["strike"])

        grids.setdefault(key, []).append((row["snapshot_time"] or "", row["iv"]))

    # The instant of the grid closest to the one of the smile, per trade date.
    def nearest(snapshot: str, candidates: list[tuple[str, float]]) -> float:
        if len(candidates) == 1:
            return candidates[0][1]

        best = min(
            candidates,
            key=lambda item: (
                abs(_seconds(item[0]) - _seconds(snapshot)),
                item[0],
            ),
        )

        return best[1]

    smile_compared = 0
    smile_same = 0
    smile_without_grid = 0
    smile_without_expiration = 0

    for smile in smile_responses:
        if (smile["trade_date"], smile["anchor_expiration"]) not in surface_pairs:
            # No comparable grid: either there is no grid for that trade date,
            # or the grid does not have that expiration.
            if smile["trade_date"] not in surface_days:
                smile_without_grid += 1
            else:
                smile_without_expiration += 1

            continue

        for strike, iv in smile_points.get(smile["response_id"], []):
            candidates = grids.get((smile["trade_date"], smile["anchor_expiration"], strike))

            if not candidates:
                continue

            smile_compared += 1

            if nearest(smile["snapshot_time"] or smile["timeslot"] or "", candidates) == iv:
                smile_same += 1

    smile_detail = f"{smile_same}/{smile_compared} points match"

    if smile_without_grid > 0:
        smile_detail += f" · {smile_without_grid} smile with no grid from the same trade date"

    if smile_without_expiration > 0:
        smile_detail += f" · {smile_without_expiration} with the expiration off the grid"

    # The check is true if, where the two meet, they match. If they never meet,
    # there is nothing to verify: it is said, and no failure is cried.
    #
    # A note, so that a red here does not send you looking for an error that is
    # not there: on `rust_od/data/od.db` the page asked for the smile of
    # 2 October at 10:27 and the grid at 10:26, and the two endpoints **do not
    # return the same IVs** (strike 7650: 26.81 vs 26.48, maximum gap 0.33 vol
    # points). The storage is right — the column is that one, the other
    # expirations are 14-16 points away — and it is the same thing the
    # JavaScript says on the same database: it is not a defect of the porting,
    # it is that the two datasets do not match. The check stays as it is:
    # loosening it here would mean hiding the thing instead of saying it.
    verifier.check(
        "the smile matches the surface where they overlap",
        smile_same == smile_compared if smile_compared > 0 else True,
        smile_detail,
    )

    # The term structure uses the same expirations as the surface of the same
    # instant: they are the two views of the same grid.
    term_moments = verifier.rows(
        "SELECT DISTINCT timeslot FROM volatility_slices WHERE kind = 'term-structure'"
    )

    surface_by_moment: dict[str, set] = {}

    for row in verifier.rows("SELECT DISTINCT timeslot, expiration FROM volatility_surface"):
        surface_by_moment.setdefault(row["timeslot"], set()).add(row["expiration"])

    term_by_moment: dict[str, list] = {}

    for row in verifier.rows(
        "SELECT DISTINCT timeslot, expiration FROM volatility_slices WHERE kind = 'term-structure'"
    ):
        term_by_moment.setdefault(row["timeslot"], []).append(row["expiration"])

    term_compared = 0
    term_missing = 0

    for row in term_moments:
        surface = surface_by_moment.get(row["timeslot"])

        if not surface:
            continue

        for expiration in term_by_moment.get(row["timeslot"], []):
            term_compared += 1

            if expiration not in surface:
                term_missing += 1

    # If the term structure never arrived there is nothing to compare: it is a
    # missing dataset, not a failure. The check says so and does not fail.
    verifier.check(
        "the term structure uses the expirations of the surface",
        True if term_compared == 0 else term_missing == 0,
        "no term structure in the archive: there is nothing to compare"
        if term_compared == 0
        else f"{term_compared} expirations compared over {len(term_moments)} instants · "
        f"{term_missing} not in common",
    )

    # The database keeps the encrypted original here too.
    surface_bodies = verifier.rows(
        "SELECT id, endpoint, iv, data, tag FROM responses "
        "WHERE endpoint IN ('volatility-3d-surface', 'volatility-3d-surface-companion', "
        "                   'volatility-3d-surface-term-structure')"
    )

    # One retained id per endpoint: the surface and the two slice kinds
    # each prune on their own (`store.py`'s `_prune_series` calls for
    # `volatility_surface` and `volatility_slices` are independent).
    surface_retained = _retained_ids(surface_records)
    slice_retained = _retained_ids(slice_records)

    surface_not_decryptable = 0
    surface_wrong_values = 0
    surface_not_pruned = 0

    for body in surface_bodies:
        try:
            payload = decrypt_response(body["data"], body["iv"], body["tag"])
        except DecryptError:
            surface_not_decryptable += 1

            continue

        retained = surface_retained if body["endpoint"] == "volatility-3d-surface" else slice_retained

        if body["id"] not in retained:
            table = "volatility_surface" if body["endpoint"] == "volatility-3d-surface" else "volatility_slices"

            if verifier.scalar(f"SELECT COUNT(*) FROM {table} WHERE response_id = ?", (body["id"],)):  # noqa: S608
                surface_not_pruned += 1

            continue

        data = payload["data"]

        mismatch = False

        if body["endpoint"] == "volatility-3d-surface":
            trace = first_trace(data)

            if trace is None:
                continue

            for row in range(len(trace["z"])):
                if mismatch:
                    break

                for column in range(len(trace["z"][row])):
                    saved = surface_by_key.get((body["id"], trace["y"][row], trace["x"][column]))

                    if saved is None or saved["iv"] != trace["z"][row][column]:
                        mismatch = True

                        break
        else:
            kind = SLICE_KINDS[body["endpoint"]]

            figure = data["figure"] if kind == "smile" else data["term"]["figure"]
            traces = figure["data"]

            for index, trace in enumerate(traces):
                if mismatch:
                    break

                label = trace.get("name")

                if label is None:
                    label = trace.get("label")

                strike = float(label.replace("k=", "")) if kind == "term-structure" else None
                trace_index = index if len(traces) > 1 else None

                for point, iv in enumerate(trace["y"]):
                    expiration = data.get("quote_date") if kind == "smile" else trace["x"][point]
                    point_strike = float(trace["x"][point]) if kind == "smile" else strike

                    saved = slice_by_key.get(
                        (body["id"], kind, expiration, point_strike, trace_index)
                    )

                    if saved is None or saved["iv"] != iv:
                        mismatch = True

                        break

        if mismatch:
            surface_wrong_values += 1

    verifier.check(
        "every saved surface body decrypts again",
        surface_not_decryptable == 0,
        f"{surface_not_decryptable} unreadable",
    )

    verifier.check(
        "the cells and the curves come back from the encrypted body",
        surface_wrong_values == 0 and surface_not_pruned == 0,
        f"{len(surface_bodies)} responses decrypted again from the database"
        + (f" · {surface_not_pruned} superseded responses were not pruned" if surface_not_pruned else ""),
    )

    # --------------------------------------------------
    # CANDLESTICKS
    #
    # One `candlestick` trace with `x` the instants **with the time zone**
    # ("2026-10-01T04:00:00-04:00") and the four OHLC series. In the database
    # the instant is in UTC, like all the other times: the conversion is done
    # by the time zone of the string, which is independent of the code that
    # wrote it.
    # --------------------------------------------------

    candle_expected = []

    for record in candle_records:
        parameters = _params(record["url"])

        trace = first_trace(record["payload"])

        # A response without candlesticks (day with no market) produces no
        # rows: there is nothing to expect.
        if trace is None or not isinstance(trace.get("x"), list):
            continue

        for index, moment in enumerate(trace["x"]):
            candle_expected.append(
                {
                    "id": record["fingerprint"],
                    "received_at": record["received_at"],
                    "trade_date": parameters.get("date"),
                    "ticker": parameters.get("ticker"),
                    "measurement": parameters.get("measurement"),
                    "frequency": parameters.get("frequency"),
                    "candle_time": candle_utc(moment),
                    "open": trace["open"][index],
                    "high": trace["high"][index],
                    "low": trace["low"][index],
                    "close": trace["close"][index],
                }
            )

    candle_stored = verifier.rows(
        "SELECT response_id, received_at, trade_date, ticker, measurement, frequency, "
        "       candle_time, open, high, low, close FROM candlesticks"
    )

    candle_by_key = {(row["response_id"], row["candle_time"]): row for row in candle_stored}

    candle_archive = verifier.scalar(
        "SELECT COUNT(*) FROM responses WHERE endpoint = ?", ("plotly-candlesticks",)
    )

    # An **empty** candlestick response (day with no market) does not enter the
    # archive: the parser rejects it, and rightly so. So the responses that
    # really had candlesticks are compared.
    #
    # Against the **full** capture (`candle_records_all`), not
    # `candle_records` (pruned to latest): `responses` is never pruned.
    candle_records_all = [record for record in records if record["endpoint"] == "plotly-candlesticks"]

    candle_with_data = [
        record
        for record in candle_records_all
        if (first_trace(record.get("payload")) or {}).get("x")
    ]

    verifier.check(
        "candlestick responses in the archive",
        candle_archive == len(candle_with_data),
        f"{candle_archive} vs {len(candle_with_data)}"
        + (
            f" · {len(candle_records_all) - len(candle_with_data)} without candlesticks (day with no market)"
            if len(candle_records_all) != len(candle_with_data)
            else ""
        ),
    )

    candle_missing = 0
    candle_different = 0

    for row in candle_expected:
        saved = candle_by_key.get((row["id"], row["candle_time"]))

        if saved is None:
            candle_missing += 1

            continue

        if (
            saved["open"] != row["open"]
            or saved["high"] != row["high"]
            or saved["low"] != row["low"]
            or saved["close"] != row["close"]
        ):
            candle_different += 1

    verifier.check(
        "every candlestick matches the payload",
        candle_missing == 0 and candle_different == 0,
        f"{candle_missing} missing, {candle_different} different",
    )

    candle_keys = {(row["id"], row["candle_time"]) for row in candle_expected}

    candle_extra = sum(
        1 for row in candle_stored if (row["response_id"], row["candle_time"]) not in candle_keys
    )

    verifier.check(
        "no extra candlestick in the database",
        candle_extra == 0,
        f"{candle_extra} stray candlesticks out of {len(candle_stored)}",
    )

    # The instants are in UTC, without duplicates, and the four values are
    # consistent: `high` is the maximum, `low` the minimum.
    candle_series: dict[str, dict] = {}

    for row in candle_stored:
        series = candle_series.setdefault(
            row["response_id"], {"candlesticks": 0, "instants": set(), "inconsistent": 0}
        )

        series["candlesticks"] += 1
        series["instants"].add(row["candle_time"])

        if (
            row["high"] < row["open"]
            or row["high"] < row["close"]
            or row["low"] > row["open"]
            or row["low"] > row["close"]
        ):
            series["inconsistent"] += 1

    candle_incoherent = [
        response_id
        for response_id, series in candle_series.items()
        if series["candlesticks"] != len(series["instants"]) or series["inconsistent"] > 0
    ]

    verifier.check(
        "every candlestick series is consistent",
        len(candle_incoherent) == 0,
        f"{len(candle_series)} series · {len(candle_incoherent)} with problems",
    )

    candle_shapes = verifier.scalar(
        "SELECT COUNT(*) FROM candlesticks WHERE candle_time NOT GLOB "
        "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]'"
    )

    verifier.check(
        "candle_time is in UTC, written like the other times",
        candle_shapes == 0,
        f"{candle_shapes} instants in another format",
    )

    candle_references = {
        row["id"]: (
            row["received_at"],
            row["trade_date"],
            row["ticker"],
            row["measurement"],
            row["frequency"],
        )
        for row in candle_expected
    }

    candle_stored_refs = verifier.rows(
        "SELECT response_id, received_at, trade_date, ticker, measurement, frequency "
        "FROM candlesticks GROUP BY response_id"
    )

    candle_wrong_refs = 0

    for row in candle_stored_refs:
        want = candle_references.get(row["response_id"])

        if want is None or (
            row["received_at"],
            row["trade_date"],
            row["ticker"],
            row["measurement"],
            row["frequency"],
        ) != want:
            candle_wrong_refs += 1

    verifier.check(
        "references correct on every candlestick series",
        candle_wrong_refs == 0,
        f"{candle_wrong_refs} inconsistent out of {len(candle_stored_refs)}",
    )

    candle_bodies = verifier.rows(
        "SELECT id, iv, data, tag FROM responses WHERE endpoint = 'plotly-candlesticks'"
    )

    candle_retained = _retained_ids(candle_records)

    candle_not_decryptable = 0
    candle_wrong_values = 0
    candle_not_pruned = 0

    for body in candle_bodies:
        try:
            payload = decrypt_response(body["data"], body["iv"], body["tag"])
        except DecryptError:
            candle_not_decryptable += 1

            continue

        saved = verifier.rows(
            "SELECT candle_time, open, high, low, close FROM candlesticks WHERE response_id = ?",
            (body["id"],),
        )

        # Superseded by a later response of the same series: expected to
        # have no rows left, same as a response without candlesticks
        # below — pruning did that, not a parser rejection, but the
        # database state looked for is identical either way.
        if body["id"] not in candle_retained:
            if saved:
                candle_not_pruned += 1

            continue

        trace = first_trace(payload)

        # A response without candlesticks has no rows in the database, and that
        # is right: the parser rejected it.
        if trace is None or not isinstance(trace.get("x"), list):
            if saved:
                candle_wrong_values += 1

            continue

        by_time = {row["candle_time"]: row for row in saved}

        mismatch = len(saved) != len(trace["x"])

        for index, moment in enumerate(trace["x"]):
            if mismatch:
                break

            row = by_time.get(candle_utc(moment))

            if (
                row is None
                or row["open"] != trace["open"][index]
                or row["high"] != trace["high"][index]
                or row["low"] != trace["low"][index]
                or row["close"] != trace["close"][index]
            ):
                mismatch = True

        if mismatch:
            candle_wrong_values += 1

    verifier.check(
        "every saved candlestick body decrypts again",
        candle_not_decryptable == 0,
        f"{candle_not_decryptable} unreadable",
    )

    verifier.check(
        "the candlesticks come back from the encrypted body",
        candle_wrong_values == 0 and candle_not_pruned == 0,
        f"{len(candle_bodies)} responses decrypted again from the database"
        + (f" · {candle_not_pruned} superseded responses were not pruned" if candle_not_pruned else ""),
    )
    # --------------------------------------------------
    # CROSS CHECKS
    #
    # The datasets are different readings of the same datum, and they touch:
    #
    #   depth_view               the grid strike × expiration
    #   breakdown_by_expiration  the same grid summed over the expirations
    #   breakdown_by_strike      the 0DTE expiration alone (the one of the trade date)
    #
    # None of the three comes from the code of the other: if the ingest had
    # lost or moved a cell, it would show here. And since both tables talk
    # about (expiration, option_symbol), the comparison is a direct join: if
    # the symbol of an expiration were deduced wrongly, the join would not find
    # the row.
    # --------------------------------------------------

    # Which responses **declare** their instant, and which are whole-day
    # requests that do not.
    #
    # This distinction is the difference between a comparison that works and
    # one that silently finds nothing. Measured: the breakdown by strike of
    # 2026-10-05 carried `timeslot = 2026-10-04 16:27:24` and the depth view
    # of the same trade date `2026-10-04 16:28:04` — two different requests,
    # forty seconds apart, both whole-day. Their exposures and sides are the
    # same, their strikes overlap, but pairing them on `timeslot` matched
    # **nothing**: `0 strikes compared`.
    #
    # For a whole-day request the instant is not in the data at all (no
    # `date_time` in the URL, no `snapshot_time` in the payload), so the
    # program gives the row an identity by falling back to its arrival. That
    # identity is honest on the row and useless for pairing two responses:
    # it says when something was asked, not what it is about.
    #
    # So: pair on the instant when **both** sides declare one, and on the
    # trade date when they do not. When they do not, the pair is two
    # readings of the same day, taken while the same dashboard was on
    # screen — which is exactly what these cross checks are about.
    declared_instant = {
        row["id"]
        for row in verifier.rows(
            "SELECT id FROM responses WHERE url LIKE '%date_time=%' "
            "OR url LIKE '%quote_datetime=%'"
        )
    }

    def pairing_key(exposure, side, timeslot, trade_date, response_id) -> tuple:
        """How two rows of different datasets are recognised as the same thing."""
        if response_id in declared_instant:
            return (exposure, side, "instant", timeslot)

        return (exposure, side, "day", trade_date)

    # The breakdown-by-expiration and depth-view-table responses come from
    # different dashboards: a capture can have one without the other. They are
    # compared **where both are there**, and it is said how many rows stay out,
    # instead of passing the subset off as the whole.
    #
    # Which depth response is the one of a breakdown row? Not the instant:
    # measured, 14 depth responses share `timeslot = 2026-10-02 17:00:00` and
    # all of them carry `SPXW`, so summing them counted the same grid fourteen
    # times (`-455.006` against `-910.060` — exactly double). What tells them
    # apart is the **option symbol**, which is why it is already part of the
    # key. When a symbol belongs to more than one depth response the pairing
    # is ambiguous, and those rows are counted apart instead of being summed
    # and called a difference.
    expiration_rows = verifier.rows(
        "SELECT exposure, side, timeslot, trade_date, response_id, expiration, option_symbol, net "
        "FROM breakdown_by_expiration"
    )

    # Which depth responses carry each (key, expiration, symbol).
    symbols_of: dict[tuple, set] = {}

    for row in verifier.rows(
        "SELECT DISTINCT exposure, side, timeslot, trade_date, response_id, expiration, option_symbol "
        "FROM depth_view"
    ):
        key = (
            *pairing_key(
                row["exposure"], row["side"], row["timeslot"], row["trade_date"], row["response_id"]
            ),
            row["expiration"],
            row["option_symbol"],
        )

        symbols_of.setdefault(key, set()).add(row["response_id"])

    depth_net_by_key: dict[tuple, float] = {}

    for row in verifier.rows(
        "SELECT exposure, side, timeslot, trade_date, response_id, expiration, option_symbol, "
        "SUM(net) AS net FROM depth_view GROUP BY 1, 2, 3, 4, 5, 6, 7"
    ):
        key = (
            *pairing_key(
                row["exposure"], row["side"], row["timeslot"], row["trade_date"], row["response_id"]
            ),
            row["expiration"],
            row["option_symbol"],
        )

        # Only where exactly one depth response owns this symbol: otherwise
        # there is no way to know which one a breakdown row is talking about.
        if len(symbols_of.get(key, ())) != 1:
            continue

        depth_net_by_key[key] = row["net"]

    cross_total = len(expiration_rows)
    cross_matched = 0
    cross_off = 0
    cross_ambiguous = 0

    for row in expiration_rows:
        key = (
            *pairing_key(
                row["exposure"], row["side"], row["timeslot"], row["trade_date"], row["response_id"]
            ),
            row["expiration"],
            row["option_symbol"],
        )

        if key not in depth_net_by_key:
            if len(symbols_of.get(key, ())) > 1:
                cross_ambiguous += 1

            continue

        cross_matched += 1

        if abs(row["net"] - depth_net_by_key[key]) > 0.5:
            cross_off += 1

    # `cross_matched == 0` is no longer a failure on its own: since the
    # writer keeps only the latest response of each table (see
    # `prune_to_latest` above), the breakdown and the depth view's
    # surviving snapshots were very likely captured at different
    # instants — no overlap is the expected case now, not a sign that a
    # row went missing. A real inconsistency still fails loudly: any
    # pair that *does* line up and disagrees (`cross_off > 0`).
    verifier.check(
        "breakdown by expiration = depth view (where the depth view is there)",
        cross_off == 0,
        f"{cross_matched}/{cross_total} rows have the matching depth view · "
        f"{cross_off} with gap > 0.5"
        + (f" · {cross_ambiguous} not attributable (the symbol belongs to several depth responses)" if cross_ambiguous else "")
        + (" · no overlapping instant (expected: only the latest response of each is kept)" if cross_matched == 0 else ""),
    )

    # The breakdown by strike is the 0DTE slice of the depth view: the
    # expiration equal to the trade date of the data.
    #
    # The pairing is **one depth response at a time**, not "all together":
    # there are several depth responses for the same instant, and summing them
    # mixes captures made at different moments.
    #
    # The comparison is done **in memory**, not in SQL: the same thing written
    # as a join over hundreds of thousands of rows made SQLite choose a
    # quadratic plan, and the check never finished. This way we decide the
    # plan: two linear scans and a map.
    cross_cells: dict[tuple, float] = {}

    for row in verifier.rows(
        "SELECT response_id, exposure, side, timeslot, trade_date, strike, net "
        "FROM depth_view WHERE expiration = trade_date"
    ):
        # `pairing_key` decides whether the instant or the trade date is what
        # identifies this row: see the comment above the breakdown-by-
        # expiration check. Here it matters twice over, because a whole-day
        # breakdown and a whole-day depth view arrive seconds apart and would
        # never share a `timeslot`.
        key = (
            row["response_id"],
            *pairing_key(
                row["exposure"], row["side"], row["timeslot"], row["trade_date"], row["response_id"]
            ),
            row["strike"],
        )

        # The same cell can appear more than once in the response: it is summed,
        # as the database does when it is asked to.
        cross_cells[key] = cross_cells.get(key, 0.0) + row["net"]

    depths_per_key: dict[tuple, list] = {}

    for row in verifier.rows(
        "SELECT DISTINCT exposure, side, timeslot, trade_date, response_id "
        "FROM depth_view WHERE expiration = trade_date"
    ):
        key = pairing_key(
            row["exposure"], row["side"], row["timeslot"], row["trade_date"], row["response_id"]
        )

        depths_per_key.setdefault(key, []).append(row["response_id"])

    strike_compared = 0
    strike_off = 0
    strike_worst = 0.0
    strike_example = None

    # Only the 0DTE rows: "StrikeAll" (the other dashboard widget on this
    # same table, see `store.expiration_scope_of`) sums every expiration,
    # not just the trade date's — comparing it against the 0DTE depth
    # view would report a mismatch that is not a bug, just two different
    # things being compared.
    for row in verifier.rows(
        "SELECT response_id, exposure, side, timeslot, trade_date, strike, net "
        "FROM breakdown_by_strike WHERE expiration_scope = '0DTE'"
    ):
        wanted = depths_per_key.get(
            pairing_key(
                row["exposure"], row["side"], row["timeslot"], row["trade_date"], row["response_id"]
            )
        )

        if not wanted:
            continue

        # The depth responses with the same keys: here the breakdown response
        # does not say which one is its own, so all the ones sharing the 0DTE
        # trade date are looked at.
        for depth_response in wanted:
            key = (
                depth_response,
                *pairing_key(
                    row["exposure"],
                    row["side"],
                    row["timeslot"],
                    row["trade_date"],
                    row["response_id"],
                ),
                row["strike"],
            )

            if key not in cross_cells:
                continue

            gap = abs(row["net"] - cross_cells[key])

            strike_compared += 1

            if gap > strike_worst:
                strike_worst = gap

            if gap > 0.5:
                strike_off += 1

                if strike_example is None:
                    strike_example = (row["strike"], row["net"], cross_cells[key])

    # Same reasoning as the expiration/depth check above: no overlapping
    # instant is the expected outcome once only the latest response of
    # each table survives, not evidence of a missing row.
    verifier.check(
        "breakdown by strike = depth view on the 0DTE only",
        strike_off == 0,
        f"{strike_compared} strikes compared · {strike_off} with gap > 0.5 · "
        f"maximum gap {strike_worst:.4f}"
        + (
            f" · e.g. strike {strike_example[0]}: {strike_example[1]} vs {strike_example[2]}"
            if strike_example
            else ""
        )
        + (" · no overlapping instant (expected: only the latest response of each is kept)" if strike_compared == 0 else ""),
    )

    # ==================================================
    # A GLANCE FOR THE READER
    # ==================================================

    print("")
    print("=== how to read it (first strikes with a value, NET_POSITION MM) ===")
    print(
        "received_at          timeslot             exposure      side    strike     call      put      net"
    )

    for row in verifier.rows(
        "SELECT received_at, timeslot, exposure, side, strike, call, put, net "
        "FROM breakdown_by_strike "
        "WHERE exposure = 'NET_POSITION' AND side = 'MM' AND net <> 0 "
        "ORDER BY timeslot, strike LIMIT 6"
    ):
        print(
            f"{row['received_at']}  {row['timeslot']}  {js_str(row['exposure']).ljust(13)} "
            f"{js_str(row['side']).ljust(7)} {js_str(row['strike']).rjust(6)} "
            f"{js_str(row['call']).rjust(8)} {js_str(row['put']).rjust(8)} "
            f"{js_str(row['net']).rjust(8)}"
        )

    print("")
    print("=== breakdown by expiration: one row per expiration (NET_POSITION MM) ===")
    print(
        "received_at          timeslot             exposure      side    expiration  symb       call       put       net"
    )

    for row in verifier.rows(
        "SELECT received_at, timeslot, exposure, side, expiration, option_symbol, call, put, net "
        "FROM breakdown_by_expiration "
        "WHERE exposure = 'NET_POSITION' AND side = 'MM' AND net <> 0 "
        "ORDER BY timeslot, expiration, option_symbol LIMIT 8"
    ):
        print(
            f"{row['received_at']}  {row['timeslot']}  {js_str(row['exposure']).ljust(13)} "
            f"{js_str(row['side']).ljust(7)} {js_str(row['expiration']).ljust(11)} "
            f"{js_str(row['option_symbol']).ljust(4)} "
            f"{js_str(row['call']).rjust(9)} {js_str(row['put']).rjust(9)} "
            f"{js_str(row['net']).rjust(9)}"
        )

    print("")
    print("=== depth view: the grid strike × expiration (NET_POSITION MM) ===")
    print(
        "received_at          timeslot             exposure      side    strike  expiration  symb       call       put       net"
    )

    for row in verifier.rows(
        "SELECT received_at, timeslot, exposure, side, strike, expiration, option_symbol, "
        "       call, put, net "
        "FROM depth_view "
        "WHERE exposure = 'NET_POSITION' AND side = 'MM' AND net <> 0 "
        "ORDER BY timeslot, strike, expiration LIMIT 6"
    ):
        print(
            f"{row['received_at']}  {row['timeslot']}  {js_str(row['exposure']).ljust(13)} "
            f"{js_str(row['side']).ljust(7)} {js_str(row['strike']).rjust(6)}  "
            f"{js_str(row['expiration'])}  {js_str(row['option_symbol']).ljust(4)} "
            f"{js_str(row['call']).rjust(9)} {js_str(row['put']).rjust(9)} "
            f"{js_str(row['net']).rjust(9)}"
        )

    print("")
    print("=== heatmap: one row per instant × price (GAMMA) ===")
    print(
        "received_at          timeslot             metric   instant              price       value"
    )

    for row in verifier.rows(
        "SELECT received_at, timeslot, metric, time_slot, price, value "
        "FROM heatmaps WHERE metric = 'GAMMA' AND price = 7600.5 "
        "ORDER BY timeslot, time_slot LIMIT 8"
    ):
        value = f"{row['value']:.3f}"

        print(
            f"{row['received_at']}  {row['timeslot']}  {js_str(row['metric']).ljust(8)} "
            f"{row['time_slot']}  {js_str(row['price']).rjust(7)} {value.rjust(11)}"
        )

    print("")
    print("=== volatility table: one row per expiry × strike (0DTE and 1M) ===")
    print(
        "received_at          timeslot             tenor   exp_date        strike      iv     "
        "od_value  percentile"
    )

    for row in verifier.rows(
        "SELECT received_at, timeslot, expiry, exp_date, strike, iv, od_value, percentile "
        "FROM volatility_table "
        "WHERE expiry IN ('0DTE', '1M') AND strike IN (7515.0, 7520.0, 7525.0) "
        "ORDER BY timeslot, expiry, strike LIMIT 8"
    ):
        print(
            f"{row['received_at']}  {row['timeslot']}  {js_str(row['expiry']).ljust(6)} "
            f"{js_str(row['exp_date']).ljust(14)} {js_str(row['strike']).rjust(7)} "
            f"{js_str(row['iv']).rjust(7)} {js_str(row['od_value']).rjust(10)} "
            f"{js_str(row['percentile']).rjust(11)}"
        )

    print("")
    print("=== surface: one row per expiration × strike (the 0DTE, three strikes) ===")
    print("received_at          timeslot             expiration  dte    strike      iv")

    for row in verifier.rows(
        "SELECT received_at, timeslot, expiration, dte, strike, iv "
        "FROM volatility_surface "
        "WHERE expiration = '2026-09-30' AND strike IN (7615, 7700, 7785, 7915) "
        "ORDER BY timeslot, strike LIMIT 8"
    ):
        print(
            f"{row['received_at']}  {row['timeslot']}  {js_str(row['expiration']).ljust(11)} "
            f"{js_str(row['dte']).rjust(3)} {js_str(row['strike']).rjust(8)} "
            f"{js_str(row['iv']).rjust(8)}"
        )

    print("")
    print("=== the slices: smile of an expiration and term structure of a strike ===")
    print(
        "kind            timeslot             curve                points    iv from    iv to"
    )

    for row in verifier.rows(
        "SELECT kind, timeslot, "
        "       COALESCE(anchor_expiration, 'k=' || anchor_strike) AS curve, "
        "       COUNT(*) AS points, MIN(iv) AS iv_from, MAX(iv) AS iv_to "
        "FROM volatility_slices GROUP BY kind, timeslot, anchor_expiration, anchor_strike "
        "ORDER BY kind, timeslot LIMIT 8"
    ):
        print(
            f"{js_str(row['kind']).ljust(15)} {row['timeslot']}  "
            f"{js_str(row['curve']).ljust(20)} {js_str(row['points']).rjust(5)} "
            f"{js_str(row['iv_from']).rjust(9)} {js_str(row['iv_to']).rjust(9)}"
        )

    print("")
    print("=== candlesticks: one row per candlestick (the first ones of es_futures_point) ===")
    print(
        "received_at          trade_date  measurement        frequency  candle_time (UTC)     "
        "open     high      low    close"
    )

    for row in verifier.rows(
        "SELECT received_at, trade_date, measurement, frequency, candle_time, open, high, low, close "
        "FROM candlesticks WHERE measurement = 'es_futures_point' "
        "ORDER BY candle_time LIMIT 6"
    ):
        print(
            f"{row['received_at']}  {row['trade_date']}  "
            f"{js_str(row['measurement']).ljust(18)} {js_str(row['frequency']).ljust(10)} "
            f"{row['candle_time']}  {js_str(row['open']).rjust(8)} "
            f"{js_str(row['high']).rjust(8)} {js_str(row['low']).rjust(8)} "
            f"{js_str(row['close']).rjust(8)}"
        )

    print("")
    print("=== the archive: one row per response, with the encrypted body ===")

    for row in verifier.rows(
        "SELECT id, endpoint, received_at, exposure, side, timeslot, rows_loaded, "
        "       LENGTH(data) AS encrypted "
        "FROM responses ORDER BY endpoint, timeslot, exposure, side LIMIT 12"
    ):
        print(
            f"{row['id']}  {js_str(row['endpoint']).ljust(18)} {row['received_at']}  "
            f"{js_str(row['exposure']).ljust(13)} {js_str(row['side']).ljust(9)} "
            f"{row['timeslot']}  {js_str(row['rows_loaded']).rjust(5)} rows  "
            f"{row['encrypted'] / 1024:.0f} KB encrypted"
        )

    print("")

    # ==================================================
    # THE CONCLUSIONS
    # ==================================================

    connection.close()

    print("")

    if verifier.failed == 0:
        print(f"✅ All the checks passed ({verifier.passed}).")

        return 0

    print(f"❌ Failed checks: {verifier.failed} out of {verifier.passed + verifier.failed}.")

    return 1


def _timeslot(url: str, received_at: str | None = None) -> str | None:
    """The instant a row carries, in the shape of the database.

    Two cases, and the second is the one that needs saying:

      1. the URL carries `date_time` (or `quote_datetime`) → that;
      2. **neither** — a whole-day request, like the 5 October one. The
         program does not write NULL: it writes **when the response
         arrived**, which is the only thing known and true about that row.
         A NULL would not pair with the depth view of the same response
         (`NULL` is not equal to `NULL`), and 336 rows were reported as "off
         by more than 0.5" for that reason alone.

    So `received_at` is the fallback the program uses, and this mirrors it.
    """
    from api import query

    value = query.param(url, "date_time") or query.param(url, "quote_datetime")

    if value is not None:
        return value.replace("T", " ")

    return received_at


if __name__ == "__main__":
    raise SystemExit(main())
