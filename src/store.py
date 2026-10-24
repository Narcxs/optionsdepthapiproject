"""From the decrypted payload to the rows of the database.

One dataset at a time, in the same shape: **a row reads by itself**,
without joins, and carries its own references. So far:

  breakdown_by_strike        `figure.data[]` → one row per strike
  breakdown_by_expiration    `figure.data[]` → one row per expiration
  depth_view                 `data[]` → one cell per strike × expiration
  heatmaps                   `figure.data[].z` → one cell per instant × price
  volatility_table           `data.data[]` → one cell per expiry × strike
  volatility_surface         `data.figure.data[0].z` → one cell per expiration × strike
  volatility_slices          the curves: one point per expiration (term structure)
                             or per strike (companion)
  candlesticks               `figure.data[0].{x,open,high,low,close}` →
                             one row per candle

The identity of the series **does not** live in the payload but in the
URL: `metric`, `participants`, `date`, `date_time`, `ticker`. The
heatmaps are the exception, halfway: the metric is not a parameter, the
endpoint says it (gamma, charm, vanna).

Tables:

  responses                  what that response was, with the encrypted body
  breakdown_by_strike        strike · call · put · net
  breakdown_by_expiration    expiration · option_symbol · call · put · net
  depth_view                 strike · expiration · option_symbol · call · put · net
  heatmaps                   time_slot · price · value (per metric)
  volatility_table           expiry · strike · iv · od_value · percentile
  volatility_surface         expiration · strike · iv
  volatility_slices          anchor · point · iv (per kind of slice)
  candlesticks               candle_time · open · high · low · close

`response_id` ties the archive to the rows (and makes the load
**idempotent**: loading the same file again duplicates nothing).

## The shapes, as they have been verified

breakdown-by-strike (8,092 rows, 0 inconsistencies):

  `figure.data[]`  three traces: `Calls`, `Puts`, `Net`
  `y`              the strikes, increasing
  `customdata[i]`  the triple `[call, put, net]`, identical in all three
  `layout.xaxis.title.text`
                   the description ("Net Market Maker Delta Exposure (∆)")

breakdown-by-expiration (290 rows, 0 inconsistencies):

  `figure.data[]`  the same three traces, of type `bar`
  `x`              the expirations, as text
  `y`              the values of the three traces
  `Calls.customdata[i]`
                   the triple `[call, put, net]`, which repeats the three `y`

The label of the expiration is the one of the API **verbatim**:
"2026-10-16", or "2026-10-16, SPX" when the expiration is the monthly
one. The same trade date can therefore appear twice, and it stays two
rows.

depth-view-table (83,440 cells, 0 inconsistencies):

  `header[]`       one column per expiration: `{title, option_symbol}`;
                   the first one is the strike. `title` is the expiration
                   with the underscores ("2026_10_16"), `option_symbol`
                   tells it apart when the same date is there twice
                   (monthly SPX + SPXW)
  `data[]`         one row per strike: `{"strike": 7600,
                   "2026-10-16-SPXW": {call_value, put_value, net_value,
                   value, color, text_color}}`
  key of the cell  `{expiration}-{option_symbol}`, that is exactly the
                   `title` of the header with the underscores turned into
                   dashes

The cells that are **absent** are not invented: under an expiration the
same row may not have the key, and then that cell does not exist (it is
not in the capture). `value` repeats `net_value` and the colors are
presentation: they are not columns, they are read back from the
encrypted body when needed.

plotly-gamma / plotly-charm / plotly-vanna (102,580 cells, 0 inconsistencies):

  `figure.data[]`  four traces, but the grid is **only in the first one**
                   (`"Gamma<br>(ES futures / point)"`): the other three
                   are the lines `Peak`/`Trough`/`Zero`, without `x`, `y`,
                   `z`
  `x`              the instants "2026-09-30T09:00:00" (85 or 86, every 5 minutes)
  `y`              the prices (92, increasing)
  `z[row][col]`    the value: `z` is as long as `y`, each row as `x`
  `zmax`/`zmin`    the color scale, which does **not** clamp the values:
                   the cells can fall outside it, and then nothing is
                   touched

The metric is not in the URL: the endpoint says it (gamma, charm, vanna).

volatility-table (5,022 cells, 0 inconsistencies):

  `data.data[]`    one row per **expiry**: `{"expiry": "0DTE",
                   "exp_date": "30-Sep-2026", "7515.0": {...}}`; the
                   expiries go from `0DTE` to `29D` and then `1M`
                   (31 rows)
  `data.header[]`  the columns: the first two are text (`Expiry`,
                   `Exp. Date`), the others are the **strikes** as decimal
                   text ("7515.0"). It is the list of the columns, and
                   here it serves to check that the data do not invent
                   others
  cell             `{value, od_value, percentile, color, text_color}`:
                   `value` is the IV, `od_value` the raw value of the
                   dashboard, `percentile` the position on a 0-100 scale
  `data.snapshot_time`, `data.spot`
                   the moment of the dashboard and the underlying: they
                   are in the payload, not in the URL (which carries the
                   `quote_datetime` that was **requested**, which is not
                   the same thing)

The **volatility surface** is three endpoints, but two shapes:

  volatility-3d-surface           the grid: `data.figure.data[0]` of type
                                  `surface`, with `x` the strikes, `y` the
                                  expirations and `z[expiration][strike]`
                                  the IV
  volatility-3d-surface-companion the **smile** of one expiration: a
                                  `scatter` trace with `x` the strikes and
                                  `y` the IV. Verified: it is the row
                                  `2026-09-30` of the surface, identical
                                  value for value
  volatility-3d-surface-term-structure
                                  the **term structure** of one strike: one
                                  trace per strike (`"k=5500"`) with `x`
                                  the expirations and `y` the IV

The two families of curves end up in a single table
(`volatility_slices`) because the points are the same thing seen from two
sides; `kind` says from which one. The grid has its own
(`volatility_surface`).

The **candles** (plotly-candlesticks) are three responses for the same
trade date, one per unit of measurement (`es_futures_point`,
`es_futures_5_min`, `od_score`), like gamma/charm/vanna: a `candlestick`
trace named `OHLC` with `x` (the instants), `open`, `high`, `low`,
`close`. Their `x` is **with the time zone**
("2026-10-01T04:00:00-04:00"), unlike everything else: here it becomes
UTC, so that it is comparable with the other times of the database.

## Translation of `src/store.rs`

It is the faithful translation of the Rust module: same table names,
columns, types, primary keys and indexes, same consistency checks, same
error messages (in Italian). Only two differences, and they belong to
the language, not to the logic:

  · `SeriesHead.trade_date()` and `timeslot()` in Python cannot be named
    like the fields they read, so they are `require_trade_date()` and
    `require_timeslot()`;
  · the fields `from`/`to` of `CandleCoverage` are reserved words, and
    they become `from_time`/`to_time` (the same names as the SQL aliases).

`store_record_locked` of the Rust is not here: there it was needed
because `rusqlite::Connection` is not `Sync`, here the caller passes the
connection.
"""

from __future__ import annotations

import math
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from actions.decode import fingerprint, readable_utc
from api import query

# Schema version: changing it recreates the tables (the data are
# regenerated from the decrypted file).
SCHEMA_VERSION = 13

DEFAULT_DATABASE = "data/od.db"


class StoreError(Exception):
    """A payload cannot be interpreted, or a row cannot be written.

    In Rust it is `anyhow::Error` with `bail!`: here it is a single
    exception, with the same messages.
    """


# ======================================================
# OPENING
# ======================================================

_DROP_TABLES = """
DROP TABLE IF EXISTS breakdown_by_strike;
DROP TABLE IF EXISTS breakdown_by_expiration;
DROP TABLE IF EXISTS depth_view;
DROP TABLE IF EXISTS heatmaps;
DROP TABLE IF EXISTS volatility_table;
DROP TABLE IF EXISTS volatility_surface;
DROP TABLE IF EXISTS volatility_slices;
DROP TABLE IF EXISTS candlesticks;
DROP TABLE IF EXISTS responses;
"""

_CREATE_TABLES = """
-- The archive: one row per response, with the exact encrypted body
-- that the API sent. The identifiers say what it contains,
-- without having to decrypt it.
CREATE TABLE responses (
    -- fingerprint of iv|data|tag: it is the identity of the content
    id            TEXT PRIMARY KEY,

    received_at   TEXT NOT NULL,      -- when it arrived (UTC)
    captured_at   INTEGER NOT NULL,   -- the same thing in unix, exact

    endpoint      TEXT NOT NULL,
    ticker        TEXT,
    trade_date    TEXT,               -- the trade date of the data
    timeslot      TEXT,               -- the instant they refer to
    exposure      TEXT,               -- GEX · VEX · DEX · CEX · NET_POSITION
    side          TEXT,               -- MM · CUSTOMER
    rows_loaded   INTEGER NOT NULL DEFAULT 0,

    url           TEXT NOT NULL,

    -- the encrypted original (AES-256-GCM), as it arrived
    iv            TEXT NOT NULL,
    data          TEXT NOT NULL,
    tag           TEXT NOT NULL
);

CREATE INDEX idx_responses_when ON responses(trade_date, timeslot);
CREATE INDEX idx_responses_what ON responses(exposure, side);

-- The rows: the references are here, the row reads by itself.
CREATE TABLE breakdown_by_strike (
    response_id   TEXT NOT NULL,

    received_at   TEXT NOT NULL,
    -- Can be NULL: a whole-day request does not carry the
    -- instant (neither in the URL nor in the payload), and inventing it
    -- would be worse than leaving it empty.
    timeslot      TEXT,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,

    exposure      TEXT NOT NULL,
    side          TEXT NOT NULL,

    -- Which expirations the dashboard widget aggregated over, from
    -- `expiration_range_start`/`expiration_range_end` in the URL:
    --   ALL     no range in the URL — every expiration
    --   0DTE    range is a single day, equal to `trade_date`
    --   a literal "{start}..{end}" otherwise, so a genuine multi-day
    --   range (seen on `breakdown-by-expiration`'s URLs) is not
    --   mislabeled as one of the other two.
    -- Two dashboard widgets (e.g. "Strikes" and "StrikeAll") can target
    -- the same endpoint and differ only by this.
    expiration_scope TEXT NOT NULL,

    strike        REAL NOT NULL,
    call          REAL,
    put           REAL,
    net           REAL,

    PRIMARY KEY (response_id, strike)
);

CREATE INDEX idx_strike_what ON breakdown_by_strike(exposure, side, trade_date, timeslot);
CREATE INDEX idx_strike_strike ON breakdown_by_strike(strike);

-- "Latest snapshot matching these filters" (the API's `/strikes/latest`)
-- is always `ORDER BY received_at DESC LIMIT 1`: with `received_at`
-- trailing in the index, SQLite walks it directly in the needed order
-- and stops at the first match, instead of collecting every matching
-- row into a temp b-tree just to sort it for one. Measured on a real
-- capture: 6.5ms -> 0.008ms per call once this index exists.
CREATE INDEX idx_strike_latest ON breakdown_by_strike(exposure, side, received_at DESC);
CREATE INDEX idx_strike_scope_latest ON breakdown_by_strike(expiration_scope, received_at DESC);
-- The filter-less case (no exposure/side/scope given) still needs an
-- index to avoid a full-table scan-and-sort for a single row.
CREATE INDEX idx_strike_recent ON breakdown_by_strike(received_at DESC);

-- Breakdown by expiration: one row per expiration.
--
-- `expiration` is the date alone. The symbol sits apart because it is
-- needed for the key: the monthly expiration (SPX) can fall on the same
-- day as the weekly one (SPXW), and without the symbol the two
-- rows would collapse into one. The API attaches the symbol to the date
-- only when it is needed to tell them apart ("2026-10-16, SPX"); here it
-- is always explicit.
CREATE TABLE breakdown_by_expiration (
    response_id   TEXT NOT NULL,

    received_at   TEXT NOT NULL,
    -- Can be NULL: a whole-day request does not carry the
    -- instant (neither in the URL nor in the payload), and inventing it
    -- would be worse than leaving it empty.
    timeslot      TEXT,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,

    exposure      TEXT NOT NULL,
    side          TEXT NOT NULL,

    expiration    TEXT NOT NULL,
    option_symbol TEXT NOT NULL,

    call          REAL NOT NULL,
    put           REAL NOT NULL,
    net           REAL NOT NULL,

    PRIMARY KEY (response_id, expiration, option_symbol)
);

CREATE INDEX idx_expiration_what
    ON breakdown_by_expiration(exposure, side, trade_date, timeslot);
CREATE INDEX idx_expiration_expiration
    ON breakdown_by_expiration(expiration, option_symbol);

-- See `idx_strike_latest`: same reasoning, for `/expirations/latest`.
CREATE INDEX idx_expiration_latest ON breakdown_by_expiration(exposure, side, received_at DESC);
CREATE INDEX idx_expiration_recent ON breakdown_by_expiration(received_at DESC);

-- Depth view: one row per strike × expiration.
--
-- The expiration and the option symbol come from the header of the
-- response, and (expiration, option symbol) are the key of the cell: the
-- same date can appear twice, as the monthly SPX and as the weekly
-- SPXW. The cells that the API does not send have no row.
CREATE TABLE depth_view (
    response_id   TEXT NOT NULL,

    received_at   TEXT NOT NULL,
    -- Can be NULL: a whole-day request does not carry the
    -- instant (neither in the URL nor in the payload), and inventing it
    -- would be worse than leaving it empty.
    timeslot      TEXT,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,

    exposure      TEXT NOT NULL,
    side          TEXT NOT NULL,

    strike        REAL NOT NULL,
    expiration    TEXT NOT NULL,
    option_symbol TEXT NOT NULL,

    call          REAL NOT NULL,
    put           REAL NOT NULL,
    net           REAL NOT NULL,

    PRIMARY KEY (response_id, expiration, option_symbol, strike)
);

CREATE INDEX idx_depth_what ON depth_view(exposure, side, trade_date, timeslot);
CREATE INDEX idx_depth_expiration ON depth_view(expiration);

-- See `idx_strike_latest`: same reasoning, for `/depth/latest`.
CREATE INDEX idx_depth_latest ON depth_view(exposure, side, received_at DESC);
CREATE INDEX idx_depth_recent ON depth_view(received_at DESC);

-- Heatmap: one row per cell, that is per instant × price.
--
-- Gamma, charm and vanna are the same data in three metrics, therefore a
-- single table: `metric` tells them apart. `measurement` is the unit
-- that the API declares in the URL (es_futures_point, es_futures_5_min,
-- od_score) and `color_scale_min`/`max` the color scale — which does not
-- clamp the values: the cells can fall outside it.
CREATE TABLE heatmaps (
    response_id   TEXT NOT NULL,

    received_at   TEXT NOT NULL,
    -- Can be NULL: a whole-day request does not carry the
    -- instant (neither in the URL nor in the payload), and inventing it
    -- would be worse than leaving it empty.
    timeslot      TEXT,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,

    metric        TEXT NOT NULL,
    measurement   TEXT,

    time_slot     TEXT NOT NULL,
    price         REAL NOT NULL,
    value         REAL NOT NULL,

    color_scale_min REAL,
    color_scale_max REAL,

    PRIMARY KEY (response_id, time_slot, price)
);

CREATE INDEX idx_heatmaps_what ON heatmaps(metric, trade_date, timeslot, time_slot);
CREATE INDEX idx_heatmaps_price ON heatmaps(price);

-- See `idx_strike_latest`: same reasoning, for `/heatmaps/latest`. This
-- is the table that grows the most (one row per instant × price, three
-- metrics, all day) — the one where an unindexed sort would hurt first.
-- Two indexes, not one: `metric` alone (the common case — GAMMA vs
-- CHARM vs VANNA) does not get a free ride from an index that also
-- requires `measurement` to be equality-constrained for the trailing
-- `received_at` to stay sorted.
CREATE INDEX idx_heatmaps_metric_latest ON heatmaps(metric, received_at DESC);
CREATE INDEX idx_heatmaps_latest ON heatmaps(metric, measurement, received_at DESC);
CREATE INDEX idx_heatmaps_recent ON heatmaps(received_at DESC);

-- Volatility table: one row per expiry × strike.
--
-- `expiry` is the label of the expiry ("0DTE", "1D"… "1M") and
-- `exp_date` its date ("30-Sep-2026"): they are two different things and
-- both are needed, therefore the expiry is the key and the date is not
-- (for a given expiry the date is one alone).
--
-- `iv` is the `value` of the cell, `od_value` the raw value
-- of the dashboard, `percentile` the position on a 0-100 scale: the
-- color of the cell is not a column, it is presentation.
-- `iv`, `od_value` and `percentile` can be NULL: a cell
-- that exists may not have the data (the 0DTE with the market closed the
-- backend sends `null`, and does not send the percentile at all).
-- Declaring them NOT NULL made those rows be discarded **in
-- silence**, because the insert is `INSERT OR IGNORE`.
CREATE TABLE volatility_table (
    response_id   TEXT NOT NULL,

    received_at   TEXT NOT NULL,
    -- Can be NULL: a whole-day request does not carry the
    -- instant (neither in the URL nor in the payload), and inventing it
    -- would be worse than leaving it empty.
    timeslot      TEXT,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,

    snapshot_time TEXT,
    spot_price    REAL,
    spot_time     TEXT,

    expiry        TEXT NOT NULL,
    exp_date      TEXT NOT NULL,

    strike        REAL NOT NULL,

    iv            REAL,
    od_value      REAL,
    percentile    REAL,

    PRIMARY KEY (response_id, expiry, strike)
);

CREATE INDEX idx_volatility_what
    ON volatility_table(trade_date, timeslot, expiry);
CREATE INDEX idx_volatility_expiry ON volatility_table(expiry);

-- See `idx_strike_latest`: same reasoning, for `/volatility/table/latest`.
CREATE INDEX idx_volatility_latest ON volatility_table(trade_date, received_at DESC);
CREATE INDEX idx_volatility_recent ON volatility_table(received_at DESC);

-- Volatility surface: one row per expiration × strike.
--
-- It is the grid that the dashboard draws in 3D: `z` of the payload, with
-- the strikes on the `x` axis and the expirations on the `y` one. `dte`
-- are the days to expiration that the API sends apart (`dtes`), and
-- `range_min`/`range_max` the range that the `meta` declares (the values
-- must stay inside it: it is a check, not a limit).
CREATE TABLE volatility_surface (
    response_id   TEXT NOT NULL,

    received_at   TEXT NOT NULL,
    -- Can be NULL: a whole-day request does not carry the
    -- instant (neither in the URL nor in the payload), and inventing it
    -- would be worse than leaving it empty.
    timeslot      TEXT,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,

    snapshot_time TEXT,
    spot_price    REAL,
    spot_time     TEXT,

    expiration    TEXT NOT NULL,
    dte           REAL,
    strike        REAL NOT NULL,
    iv            REAL NOT NULL,

    x_label       TEXT,
    y_label       TEXT,
    range_min     REAL,
    range_max     REAL,

    PRIMARY KEY (response_id, expiration, strike)
);

CREATE INDEX idx_surface_what ON volatility_surface(trade_date, timeslot, expiration);
CREATE INDEX idx_surface_strike ON volatility_surface(strike);

-- See `idx_strike_latest`: same reasoning, for `/volatility/surface/latest`.
CREATE INDEX idx_surface_latest ON volatility_surface(trade_date, received_at DESC);
CREATE INDEX idx_surface_recent ON volatility_surface(received_at DESC);

-- The curves of the surface: the smile of one expiration and the term
-- structure of one strike are the same thing seen from two sides,
-- therefore a single table. `kind` says from which one:
--
--   smile           `anchor_expiration` is the expiration of the curve,
--                   `strike` the point
--   term-structure  `anchor_strike` is the strike of the curve,
--                   `expiration` the point
--
-- In both cases a row is "a point of a curve": the two
-- pairs of columns say which curve and which point.
CREATE TABLE volatility_slices (
    response_id   TEXT NOT NULL,

    received_at   TEXT NOT NULL,
    -- Can be NULL: a whole-day request does not carry the
    -- instant (neither in the URL nor in the payload), and inventing it
    -- would be worse than leaving it empty.
    timeslot      TEXT,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,

    snapshot_time TEXT,
    spot_price    REAL,
    spot_time     TEXT,

    kind          TEXT NOT NULL,

    trace_index   INTEGER,
    anchor_expiration TEXT,
    anchor_strike     REAL,

    expiration    TEXT NOT NULL,
    strike        REAL NOT NULL,
    iv            REAL NOT NULL,

    PRIMARY KEY (response_id, kind, expiration, strike, trace_index)
);

CREATE INDEX idx_slices_what ON volatility_slices(kind, trade_date, timeslot);
CREATE INDEX idx_slices_expiration ON volatility_slices(expiration);

-- See `idx_strike_latest`: same reasoning, for `/volatility/slices/latest`.
CREATE INDEX idx_slices_latest ON volatility_slices(kind, received_at DESC);
CREATE INDEX idx_slices_recent ON volatility_slices(received_at DESC);

-- Candles: one row per candle (one OHLC trace per response).
--
-- `measurement` and `frequency` come from the URL and tell the
-- responses apart: the same trade date arrives several times, one per
-- unit of measurement (es_futures_point, es_futures_5_min, od_score).
-- The time that matters is the one of each candle: `candle_time` is in
-- **UTC**, like the other times of the database, while in the API it is
-- with the zone attached.
--
-- `timeslot` is not there: the candles do not send it (neither in the URL
-- nor in the payload) and it would only be the arrival time of the
-- response, which is already `received_at`.
CREATE TABLE candlesticks (
    response_id   TEXT NOT NULL,

    received_at   TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,

    measurement   TEXT,
    frequency     TEXT,

    candle_time   TEXT NOT NULL,

    open          REAL NOT NULL,
    high          REAL NOT NULL,
    low           REAL NOT NULL,
    close         REAL NOT NULL,

    PRIMARY KEY (response_id, candle_time)
);

CREATE INDEX idx_candles_what ON candlesticks(trade_date, measurement, candle_time);

-- See `idx_strike_latest` and `idx_heatmaps_metric_latest`: same
-- reasoning, for `/candles/latest`. `measurement` alone is the common
-- case (es_futures_point vs. es_futures_5_min vs. od_score).
CREATE INDEX idx_candles_measurement_latest ON candlesticks(measurement, received_at DESC);
CREATE INDEX idx_candles_latest ON candlesticks(measurement, frequency, received_at DESC);
CREATE INDEX idx_candles_recent ON candlesticks(received_at DESC);
"""


def open_database(path: str = DEFAULT_DATABASE) -> sqlite3.Connection:
    """Opens the database, creating it and aligning the schema.

    The folder is created if it is missing; if the schema version is not
    `SCHEMA_VERSION` the data tables are dropped and made again (the data
    are regenerated from the capture).

    The connection is in **autocommit** (`isolation_level = None`): the
    transactions are opened by `_transaction`, the way rusqlite does it,
    instead of leaving the module to decide.

    And it is **shareable between threads** (`check_same_thread = False`):
    the live capture reads the response in the event loop and writes it on
    a separate thread (`asyncio.to_thread`), because `sqlite3` is
    synchronous and must not be kept on the loop. Without this permission,
    every write ends in
    `ProgrammingError: SQLite objects created in a thread can only be used
    in that same thread` — and the responses are lost.

    It is not a danger: the live pipeline has a lock (`live._pipeline_lock`)
    that serializes the whole round of a response, so two threads never use
    this connection together. It is the same pact as the Rust project,
    where the connection sits in a `Mutex`.
    """
    parent = Path(path).parent

    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)

    try:
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    except sqlite3.Error as error:
        raise StoreError(f"Cannot open the database {path}") from error

    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        """
    )

    ensure_schema(connection)

    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Creates the tables, or makes them again if the schema version changed."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )

    row = connection.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()

    current: int | None = None

    if row is not None:
        try:
            current = int(row[0])
        except (TypeError, ValueError):
            current = None

    if current == SCHEMA_VERSION:
        # The tables are already right, but rows written by an older version
        # may still carry an empty instant. That fills it, and it is
        # idempotent: after the first run there is nothing left to fill.
        _fill_empty_timeslots(connection)

        return

    connection.executescript(_DROP_TABLES)
    connection.executescript(_CREATE_TABLES)

    connection.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


# The tables that carry the instant of the dashboard.
#
# `responses` is in the list, and it was the one missing: the first version
# of this migration filled the seven data tables and left the archive with
# 68 empty slots. The archive describes the same responses, so it says the
# same instant — otherwise a check that pairs a response with its rows finds
# `NULL` on one side only.
_TIMESLOT_TABLES = (
    "responses",
    "breakdown_by_strike",
    "breakdown_by_expiration",
    "depth_view",
    "heatmaps",
    "volatility_table",
    "volatility_surface",
    "volatility_slices",
)


def _fill_empty_timeslots(connection: sqlite3.Connection) -> None:
    """Gives an instant to the rows that have an empty one.

    A whole-day request does not carry the instant of the dashboard, and the
    first version of this program wrote `NULL`. A row with `NULL` cannot be
    paired with the same instant of another table, because `NULL` is not
    equal to `NULL`: the breakdown by expiration and the depth view of the
    **same** response looked like two different moments, and the check that
    compares them reported 336 rows "off by more than 0.5" when they simply
    were not paired.

    The fallback is the one thing that is known and true about that row:
    **when the response arrived**. It is written on the row already, so this
    is a copy, not a guess.
    """
    for table in _TIMESLOT_TABLES:
        connection.execute(
            f"UPDATE {table} SET timeslot = received_at WHERE timeslot IS NULL"
        )


# ======================================================
# JSON VALUES
#
# The same helpers as serde_json (`Value::as_str`, `as_f64`, `as_i64`):
# they serve to tell "the field is not there" from "the field is there and
# is something else", which is the difference between absent data and an
# error.
# ======================================================


def _as_str(value: Any) -> str | None:
    """Like `Value::as_str`."""
    return value if isinstance(value, str) else None


def _as_f64(value: Any) -> float | None:
    """Like `Value::as_f64`: a number, but **not** a boolean.

    In JSON `true` is not `1`: serde_json does not convert it, and
    neither does this.
    """
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    return None


def _as_i64(value: Any) -> int | None:
    """Like `Value::as_i64`: only an integer (a `1.5` is not an `i64`)."""
    if isinstance(value, bool):
        return None

    return value if isinstance(value, int) else None


def _as_array(value: Any) -> list[Any] | None:
    """Like `Value::as_array`."""
    return value if isinstance(value, list) else None


def _as_object(value: Any) -> dict[str, Any] | None:
    """Like `Value::as_object`."""
    return value if isinstance(value, dict) else None


def _at(values: Sequence[Any], index: int) -> Any | None:
    """The `values.get(index)` of the Rust: `None` if the index is not there."""
    if 0 <= index < len(values):
        return values[index]

    return None


def _same_json(left: Any, right: Any) -> bool:
    """Equality like the one of serde_json.

    An integer and a decimal **are not** the same value (in Rust
    `Value::Number(1) != Value::Number(1.0)`), while in Python
    `1 == 1.0`. It is needed by the consistency checks, where two
    lists that must be the same list are compared.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right

    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return type(left) is type(right) and left == right

    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_json(one, other) for one, other in zip(left, right)
        )

    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_json(left[key], right[key]) for key in left
        )

    return left == right


def _display_number(value: float) -> str:
    """A number the way Rust prints it: `7515.0` → `"7515"`.

    It is only needed by the error messages, because the text must be the
    same: `f"{7515.0}"` in Python would give `"7515.0"`.
    """
    if math.isfinite(value) and value == int(value):
        return str(int(value))

    return repr(value)


# ======================================================
# WHAT A RESPONSE SAYS, BEFORE ITS VALUES
#
# The identity of the series is not in the payload but in the URL. The
# tables carry it row by row, so it is read once only, here, and it
# holds for all the datasets.
#
# With two exceptions, which the parsers fill from the payload:
#
#   the metric      for the breakdowns and the depth view it is the
#                   parameter `metric`, for the heatmaps the endpoint
#                   says it
#   trade date and instant
#                   the volatility does not send `date` and `date_time`
#                   in the URL: it carries `quote_date` and
#                   `snapshot_time` in the payload, and that is where
#                   they are taken from
# ======================================================


@dataclass
class SeriesHead:
    """The references of a series: who it is, when, from where."""

    # Fingerprint of the encrypted body: identity of the response.
    response_id: str

    endpoint: str

    received_at: str
    captured_at: int

    ticker: str

    # The trade date of the data and the instant they refer to. Usually
    # they come from the URL (`date`, `date_time`); whoever does not have
    # them in the URL fills them from the payload.
    trade_date: str | None
    timeslot: str | None

    # The side of the market, from `participants` (MM, CUSTOMER, MIXED…).
    # Not every endpoint sends it.
    side: str | None

    url: str

    # The encrypted original, which ends up in the database.
    iv: str
    data: str
    tag: str

    def require_trade_date(self) -> str:
        """The trade date of the data, as a string.

        In Rust it is `SeriesHead::trade_date()`; here it cannot be named
        like the field it reads, so it has a name of its own.
        """
        if self.trade_date is None:
            raise StoreError(
                "trade date absent (neither `date` in the URL nor `quote_date` in the payload)"
            )

        return self.trade_date

    def require_timeslot(self) -> str:
        """The instant of the dashboard, always "2026-09-30 13:00:00"."""
        if self.timeslot is None:
            raise StoreError(
                "instant absent (neither `date_time` in the URL nor `snapshot_time` in the payload)"
            )

        return self.timeslot

    def effective_timeslot(self) -> str:
        """The instant a row carries, and it is **never** empty.

        A whole-day request (`date=2026-10-05`, no `date_time`) does not
        carry an instant: neither in the URL nor, for most endpoints, in the
        payload. The column accepts NULL, but a row without it cannot be
        matched against the same instant of another table — `NULL` is not
        equal to `NULL` in a comparison, so the breakdown and the depth view
        of the *same* response would look like two different moments.

        Measured: with the NULL, 336 rows of `breakdown_by_expiration` were
        reported as "off by more than 0.5" against the depth view, when they
        were simply not paired. So the fallback is the one thing that is
        known and true: **when the response arrived**. It is not the instant
        of the dashboard, and it does not pretend to be — but it is written
        on the row, and it is the same on both sides of the comparison.

        Order: the URL, then the payload (fill the head with
        `_head_with_payload_moment` first), then the arrival.
        """
        return self.timeslot or self.received_at


def read_head(record: dict[str, Any], endpoint: str) -> SeriesHead:
    """Reads the URL, the encrypted body and the fingerprint of a decrypted
    response.

    `endpoint` is the token of the endpoint being interpreted: if the
    response is of another one, it does not guess.
    """
    named = _as_str(record.get("endpoint")) or ""

    if named != endpoint:
        raise StoreError(f"endpoint '{named}' instead of '{endpoint}'")

    url = _as_str(record.get("url"))

    if url is None:
        raise StoreError("url absent")

    if not isinstance(record.get("payload"), dict):
        raise StoreError("payload absent")

    params = query.params(url)

    ticker = _required(params, "ticker")

    # Trade date and instant: they are in the URL for almost every
    # endpoint, but not for all of them (the volatility carries them in
    # the payload).
    date = params.get("date") or None

    date_time = params.get("date_time") or None

    timeslot = date_time.replace("T", " ") if date_time is not None else None

    # `participants` is almost always there: the heatmaps of the surface
    # do not send it, and it is not an error.
    participants = params.get("participants")

    if participants == "mm_posn":
        side: str | None = "MM"
    elif participants:
        side = "CUSTOMER"
    else:
        side = None

    captured_at = _as_i64(record.get("captured_at")) or 0

    # The encrypted body accompanies the payload: it is the one that is
    # kept.
    iv = _text(record, "iv")
    data = _text(record, "data")
    tag = _text(record, "tag")

    # If the fingerprint is missing, it is computed: it is the same
    # fingerprint of the body.
    response_id = _as_str(record.get("fingerprint")) or fingerprint(iv, data, tag)

    received_at = _as_str(record.get("received_at")) or readable_utc(captured_at)

    return SeriesHead(
        response_id=response_id,
        endpoint=endpoint,
        received_at=received_at,
        captured_at=captured_at,
        ticker=ticker,
        trade_date=date,
        timeslot=timeslot,
        side=side,
        url=url,
        iv=iv,
        data=data,
        tag=tag,
    )


def metric_of(head: SeriesHead) -> str:
    """The metric of a response that carries it in the URL: `metric` in
    upper case.

    The heatmaps do not have it (the endpoint says it) and do not come
    through here.
    """
    params = query.params(head.url)

    return _required(params, "metric").upper()


def side_of(head: SeriesHead) -> str:
    """The side, mandatory: the datasets per strike and per expiration do
    not exist without `participants`."""
    if head.side is None:
        raise StoreError("participants absent: it is not known which side it belongs to")

    return head.side


def payload_of(record: dict[str, Any]) -> Any:
    """The payload of a decrypted response (already checked by `read_head`)."""
    payload = record.get("payload")

    if payload is None:
        raise StoreError("payload absent")

    return payload


# ======================================================
# A SERIES OF STRIKES
# ======================================================


@dataclass
class StrikeRow:
    strike: float
    call: float
    put: float
    net: float


@dataclass
class StrikeSeries:
    head: SeriesHead

    # The metric of the series (GEX, DEX…), from the URL.
    exposure: str

    # Which expirations this response was aggregated over — see
    # `expiration_scope_of`.
    expiration_scope: str

    rows: list[StrikeRow]


def expiration_scope_of(url: str, trade_date: str | None) -> str:
    """Which expirations a response was aggregated over, from the URL's
    `expiration_range_start`/`expiration_range_end`.

    Two dashboard widgets can hit the same endpoint and differ only by
    this — "Strikes" (0DTE, the range is today alone) versus "StrikeAll"
    (no range at all, every expiration). A genuine multi-day range
    (seen on `breakdown-by-expiration`) is kept literal instead of being
    forced into one of those two buckets.
    """
    params = query.params(url)

    start = params.get("expiration_range_start") or None
    end = params.get("expiration_range_end") or None

    if start is None and end is None:
        return "ALL"

    if start == trade_date and end == trade_date:
        return "0DTE"

    return f"{start or '*'}..{end or '*'}"


def parse_strike(record: dict[str, Any]) -> StrikeSeries:
    """Interprets a decrypted `breakdown-by-strike` response."""
    head = read_head(record, "breakdown-by-strike")

    exposure = metric_of(head)

    expiration_scope = expiration_scope_of(head.url, head.trade_date)

    payload = payload_of(record)

    traces = _figure_traces(payload)

    calls = _trace(traces, "Calls")
    puts = _trace(traces, "Puts")
    net = _trace(traces, "Net")

    strikes = _as_array(calls.get("y"))

    if strikes is None:
        raise StoreError("Calls.y absent")

    customdata = _as_array(calls.get("customdata"))

    if customdata is None:
        raise StoreError("Calls.customdata absent")

    calls_x = _array(calls, "x")
    puts_x = _array(puts, "x")
    net_x = _array(net, "x")

    rows: list[StrikeRow] = []

    mismatches = 0

    for index, strike in enumerate(strikes):
        strike_value = _as_f64(strike)

        if strike_value is None:
            raise StoreError(f"non-numeric strike at row {index}")

        triple = _as_array(_at(customdata, index))

        if triple is None:
            raise StoreError(f"customdata missing at row {index}")

        call = _number(_at(triple, 0))
        put = _number(_at(triple, 1))
        net_value = _number(_at(triple, 2))

        # The triple must match the three traces: if not, nothing is
        # written instead of writing something wrong.
        if (
            _as_f64(_at(calls_x, index)) != call
            or _as_f64(_at(puts_x, index)) != put
            or _as_f64(_at(net_x, index)) != net_value
        ):
            mismatches += 1

        rows.append(
            StrikeRow(strike=strike_value, call=call, put=put, net=net_value)
        )

    if mismatches > 0:
        raise StoreError(
            f"{mismatches} rows with customdata different from the traces: inconsistent response"
        )

    if not rows:
        raise StoreError("no strike in the response")

    return StrikeSeries(
        head=head, exposure=exposure, expiration_scope=expiration_scope, rows=rows
    )


# ======================================================
# A SERIES OF EXPIRATIONS (breakdown by expiration)
#
# Same figure as the breakdown by strike, but the axis is the `x`: the
# expirations are text, the values the `y`. The triple `[call, put, net]`
# is in `Calls.customdata` and repeats the three traces.
#
# ## The expiration, and the symbol that the API sticks to the date
#
# Usually the `x` is a date ("2026-10-16"). When however the **monthly**
# expiration (SPX) falls on the same day as a weekly one (SPXW),
# the API writes the date with the symbol attached: `"2026-10-16, SPX"`,
# while the weekly one stays the pure date. Verified against the depth
# view: the row with the symbol is really the SPX, the one without is the
# SPXW — they coincide on all 290 rows, maximum gap of the rounding.
#
# In the database the two things sit apart, as in the depth view:
#
#   expiration      the date alone ("2026-10-16")
#   option_symbol   SPX (monthly) or SPXW (weekly)
#
# The symbol cannot be thrown away: without it, the monthly and the weekly
# of the same day would collapse into a single row. The explicit symbol is
# the one written by the API; for the pure dates it is **SPXW**, and
# it is not a guess: it is what the depth view of the same
# response says, and the verifier checks it again row by row.
# ======================================================

# The symbol that the API leaves implicit when it writes the date alone.
IMPLICIT_OPTION_SYMBOL = "SPXW"


@dataclass
class ExpirationRow:
    """An expiration, with its symbol."""

    # The expiration date alone: "2026-10-16".
    expiration: str

    # SPX (monthly) or SPXW (weekly).
    option_symbol: str

    call: float
    put: float
    net: float


@dataclass
class ExpirationSeries:
    head: SeriesHead

    # The metric of the series (GEX, DEX…), from the URL.
    exposure: str

    rows: list[ExpirationRow]


def parse_expiration(record: dict[str, Any]) -> ExpirationSeries:
    """Interprets a decrypted `breakdown-by-expiration` response."""
    head = read_head(record, "breakdown-by-expiration")

    exposure = metric_of(head)

    payload = payload_of(record)

    traces = _figure_traces(payload)

    calls = _trace(traces, "Calls")
    puts = _trace(traces, "Puts")
    net = _trace(traces, "Net")

    labels = _array(calls, "x")

    customdata = _as_array(calls.get("customdata"))

    if customdata is None:
        raise StoreError("Calls.customdata absent")

    calls_y = _array(calls, "y")
    puts_y = _array(puts, "y")
    net_y = _array(net, "y")

    rows: list[ExpirationRow] = []

    mismatches = 0
    duplicates = 0

    seen: set[tuple[str, str]] = set()

    for index, label in enumerate(labels):
        raw = _as_str(label)

        text = raw.strip() if raw is not None else ""

        if not text:
            raise StoreError(f"empty or non-textual expiration at row {index}")

        expiration, option_symbol = split_expiration(text)

        triple = _as_array(_at(customdata, index))

        if triple is None:
            raise StoreError(f"customdata missing at row {index}")

        call = _number(_at(triple, 0))
        put = _number(_at(triple, 1))
        net_value = _number(_at(triple, 2))

        # The triple must match the three traces: if not, nothing is
        # written instead of writing something wrong.
        if (
            _as_f64(_at(calls_y, index)) != call
            or _as_f64(_at(puts_y, index)) != put
            or _as_f64(_at(net_y, index)) != net_value
        ):
            mismatches += 1

        if (expiration, option_symbol) in seen:
            duplicates += 1

        seen.add((expiration, option_symbol))

        rows.append(
            ExpirationRow(
                expiration=expiration,
                option_symbol=option_symbol,
                call=call,
                put=put,
                net=net_value,
            )
        )

    if mismatches > 0:
        raise StoreError(
            f"{mismatches} rows with customdata different from the traces: inconsistent response"
        )

    if duplicates > 0:
        raise StoreError(
            f"{duplicates} repeated expirations in the same response (same date and symbol)"
        )

    if not rows:
        raise StoreError("no expiration in the response")

    return ExpirationSeries(head=head, exposure=exposure, rows=rows)


# ======================================================
# A HEATMAP (gamma, charm, vanna)
#
# The figure has four traces, but the grid is **only in the first one**:
# the other three (`Peak`, `Trough`, `Zero`) are reference lines
# without `x`, `y` or `z`. The axes are there once per response and here
# they end up **on every row**: the grid is sparse by nature (one value
# per instant × price), so every cell carries its own time and its own
# price and a row reads by itself.
#
# The metric is not a parameter of the URL: the endpoint says it. The
# token however is not enough to identify it (gamma, charm, vanna are
# three different endpoints for the same table), so one goes through the
# endpoint.
# ======================================================


class HeatmapKind(Enum):
    """The heatmap metrics, with their endpoint and the name of the trace."""

    GAMMA = "GAMMA"
    CHARM = "CHARM"
    VANNA = "VANNA"

    def endpoint(self) -> str:
        """The endpoint that carries this metric."""
        return {
            HeatmapKind.GAMMA: "plotly-gamma",
            HeatmapKind.CHARM: "plotly-charm",
            HeatmapKind.VANNA: "plotly-vanna",
        }[self]

    def metric(self) -> str:
        """The metric as it ends up in the database and in the reports."""
        return self.value

    def trace_name(self) -> str:
        """The name of the main trace, as the API writes it
        ("Gamma<br>(ES futures / point)")."""
        return {
            HeatmapKind.GAMMA: "Gamma",
            HeatmapKind.CHARM: "Charm",
            HeatmapKind.VANNA: "Vanna",
        }[self]


@dataclass
class HeatmapRow:
    # The instant of the `x` axis: "2026-09-30 09:00:00".
    time_slot: str

    # The price of the `y` axis.
    price: float

    value: float


@dataclass
class HeatmapSeries:
    head: SeriesHead

    metric: str

    # The unit declared in the URL (`es_futures_point`, `od_score`…).
    measurement: str | None

    # The color scale, which does not clamp the values.
    color_scale_min: float | None
    color_scale_max: float | None

    rows: list[HeatmapRow]


def parse_heatmap(record: dict[str, Any], kind: HeatmapKind) -> HeatmapSeries:
    """Interprets a decrypted heatmap response (`plotly-gamma`, `-charm`,
    `-vanna`)."""
    head = read_head(record, kind.endpoint())

    payload = payload_of(record)

    traces = _figure_traces(payload)

    grid = _heatmap_grid(traces, kind)

    x = _array(grid, "x")
    y = _array(grid, "y")

    z = _as_array(grid.get("z"))

    if z is None:
        raise StoreError("z absent")

    if len(z) != len(y):
        raise StoreError(
            f"z has {len(z)} rows and y {len(y)} prices: the grid does not add up"
        )

    params = query.params(head.url)

    rows: list[HeatmapRow] = []

    short_rows = 0
    repeats = 0

    seen: set[tuple[str, int]] = set()

    for row_index, values in enumerate(z):
        cells = _as_array(values)

        if cells is None:
            raise StoreError(f"z[{row_index}] is not a row")

        if len(cells) != len(x):
            short_rows += 1

        price = _as_f64(_at(y, row_index))

        if price is None:
            raise StoreError(f"non-numeric price at row {row_index}")

        for column, value in enumerate(cells):
            # A row shorter than x is not completed by hand: the cell
            # that is missing is not there.
            if column >= len(x):
                continue

            instant = _as_str(_at(x, column))

            if instant is None:
                raise StoreError(f"non-textual instant in x[{column}]")

            time_slot = moment(instant)

            cell = _as_f64(value)

            if cell is None:
                raise StoreError(
                    f"non-numeric value in z[{row_index}][{column}]"
                )

            if (time_slot, strike_key(price)) in seen:
                repeats += 1

            seen.add((time_slot, strike_key(price)))

            rows.append(HeatmapRow(time_slot=time_slot, price=price, value=cell))

    if short_rows > 0:
        raise StoreError(f"{short_rows} rows of z do not have the length of x")

    if repeats > 0:
        raise StoreError(f"{repeats} repeated cells (same instant and price)")

    if not rows:
        raise StoreError("no cell in the response")

    return HeatmapSeries(
        head=head,
        metric=kind.metric(),
        measurement=params.get("measurement") or None,
        color_scale_min=_float_param(params, "color_scale_min"),
        color_scale_max=_float_param(params, "color_scale_max"),
        rows=rows,
    )


def _heatmap_grid(traces: list[Any], kind: HeatmapKind) -> dict[str, Any]:
    """The trace that carries the grid: it is the one with `x`, `y` and `z`.

    The other three traces (`Peak`, `Trough`, `Zero`) are reference
    lines without axes. If more than one with the grid were found, it is
    better to stop than to pick one at random.
    """
    grids = [
        trace
        for trace in traces
        if isinstance(trace, dict)
        and "x" in trace
        and "y" in trace
        and "z" in trace
    ]

    if not grids:
        raise StoreError("no trace with x, y and z: the grid is not there")

    if len(grids) > 1:
        raise StoreError(
            f"{len(grids)} traces with a grid: it is not known which one is the good one"
        )

    grid = grids[0]

    # The name of the main trace says the metric: if it does not
    # match, the endpoint and the payload do not tell the same thing.
    name = _as_str(grid.get("name")) or ""

    if not name.startswith(kind.trace_name()):
        raise StoreError(
            f"the main trace is named '{name}' and not "
            f"'{kind.trace_name()}...': endpoint and payload do not coincide"
        )

    return grid


def moment(value: str) -> str:
    """"2026-09-30T09:00:00" → "2026-09-30 09:00:00", like the `timeslot`
    of the other tables."""
    text = value.strip()

    if "T" not in text:
        raise StoreError(f"instant '{text}' is not in the format of the API")

    return text.replace("T", " ")


def _float_param(params: dict[str, str], key: str) -> float | None:
    """A numeric parameter of the URL, if it is there."""
    value = params.get(key)

    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        return None


# ======================================================
# A SERIES OF DEPTH (depth view)
#
# The grid is strike × expiration. The expirations are not in the data but
# in the `header`, and they are the key of the cells of the data: here the
# two things are compared, and if they do not coincide nothing is written.
# ======================================================


@dataclass
class DepthRow:
    strike: float

    # Expiration in ISO form: "2026-10-16".
    expiration: str

    # SPX (monthly) or SPXW (weekly): it tells apart two expirations on the
    # same day.
    option_symbol: str

    call: float
    put: float
    net: float


@dataclass
class DepthSeries:
    head: SeriesHead

    # The metric of the series (GEX, DEX…), from the URL.
    exposure: str

    rows: list[DepthRow]


@dataclass
class DepthColumn:
    """A column of the header of the depth view."""

    expiration: str
    option_symbol: str


def parse_depth(record: dict[str, Any]) -> DepthSeries:
    """Interprets a decrypted `depth-view-table` response."""
    head = read_head(record, "depth-view-table")

    exposure = metric_of(head)

    payload = payload_of(record)

    header = _as_array(payload.get("header"))

    if header is None:
        raise StoreError("header absent: it is not known which expirations there are")

    columns = _depth_columns(header)

    data = _as_array(payload.get("data"))

    if data is None:
        raise StoreError("data absent")

    rows: list[DepthRow] = []

    unknown_columns = 0
    duplicates = 0

    seen: set[tuple[str, str, int]] = set()

    # Which columns declared by the header really have cells:
    # a header wider than the data is reported, not thrown away.
    used: set[str] = set()

    for index, row in enumerate(data):
        obj = _as_object(row)

        if obj is None:
            raise StoreError(f"row {index} is not an object")

        strike = _as_f64(obj.get("strike"))

        if strike is None:
            strike = _as_f64(obj.get("strike_price"))

        if strike is None:
            raise StoreError(f"strike missing or non-numeric at row {index}")

        for key, cell in obj.items():
            if key in ("strike", "strike_price"):
                continue

            if _as_object(cell) is None:
                raise StoreError(f"cell '{key}' is not an object (row {index})")

            # The key of the data must be a declared column:
            # expiration *and* option symbol, not only the date.
            column = columns.get(key)

            if column is None:
                unknown_columns += 1

                continue

            used.add(key)

            call = _number(cell.get("call_value"))
            put = _number(cell.get("put_value"))
            net = _number(cell.get("net_value"))

            if (column.expiration, column.option_symbol, strike_key(strike)) in seen:
                duplicates += 1

            seen.add((column.expiration, column.option_symbol, strike_key(strike)))

            rows.append(
                DepthRow(
                    strike=strike,
                    expiration=column.expiration,
                    option_symbol=column.option_symbol,
                    call=call,
                    put=put,
                    net=net,
                )
            )

    if unknown_columns > 0:
        raise StoreError(
            f"{unknown_columns} cells with a column that the header does not declare"
        )

    if duplicates > 0:
        raise StoreError(f"{duplicates} repeated cells (same expiration and strike)")

    if not rows:
        raise StoreError("no cell in the response")

    empty_columns = len([key for key in columns if key not in used])

    if empty_columns > 0:
        # It is not an error: the header declares an expiration, the data do
        # not have even one cell of it. It is reported because it is a column
        # that does not end up in the database although it is declared.
        _info(f"{empty_columns} expirations declared in the header without cells")

    return DepthSeries(head=head, exposure=exposure, rows=rows)


def _depth_columns(header: list[Any]) -> dict[str, DepthColumn]:
    """The columns declared by the `header`: key of the data → expiration and
    option symbol.

    The key of the data is the `title` of the header with the underscores
    turned into dashes ("2026_10_16" → expiration, "2026-10-16-SPXW" →
    key of the cell).
    """
    columns: dict[str, DepthColumn] = {}

    without_symbol = 0

    for index, entry in enumerate(header):
        title = (_as_str(_entry_get(entry, "title")) or "").strip()

        # The first column is the strike: it is not an expiration.
        if not title or title.lower() == "strike":
            continue

        expiration = title.replace("_", "-")

        if not is_iso_date(expiration):
            raise StoreError(f"header[{index}]: '{title}' is not an expiration")

        symbol = (_as_str(_entry_get(entry, "option_symbol")) or "").strip()

        if not symbol:
            # Without an option symbol two expirations of the same day
            # would be indistinguishable: better to stop.
            without_symbol += 1

            continue

        key = f"{expiration}-{symbol}"

        previous = columns.get(key)

        if previous is not None:
            raise StoreError(
                f"header: the column '{key}' is declared twice "
                f"({previous.expiration} and {previous.option_symbol})"
            )

        columns[key] = DepthColumn(expiration=expiration, option_symbol=symbol)

    if without_symbol > 0:
        raise StoreError(f"{without_symbol} columns without option_symbol in the header")

    if not columns:
        raise StoreError("header without expirations")

    return columns


# ======================================================
# A VOLATILITY TABLE (volatility-table)
#
# The grid is expiry × strike. The expiries are the rows of
# `data.data[]` (`0DTE`, `1D`… `1M`), the strikes are the columns of
# `data.header[]`, which declares them as decimal text ("7515.0") and
# uses them as keys of the data in the same form. The two things are
# compared: if the data had a column that the header does not
# declare, or vice versa, the response is not written.
#
# Here the grid is **full** (verified: 5,022 cells out of 5,022
# possible), but the code does not take it for granted: the cells that are
# missing simply do not become rows.
# ======================================================


@dataclass
class VolatilityRow:
    """A cell of the table: its expiry and its strike."""

    # The expiry, as the API writes it: "0DTE", "1D"… "1M".
    expiry: str

    # The expiration date, as the API writes it: "30-Sep-2026".
    exp_date: str

    strike: float

    # The IV of the cell (`value`), the raw value of the dashboard
    # (`od_value`) and the position on a 0-100 scale (`percentile`).
    #
    # They are **optional**: a cell that exists may not have the data.
    # It happens for the 0DTE with the market closed, where the backend
    # sends `{"value": null, "od_value": null}` and does not send the
    # `percentile` at all. It is not an error — it is "this datum is not
    # there" — and the row is written all the same, with its `NULL`s.
    iv: float | None
    od_value: float | None
    percentile: float | None


@dataclass
class VolatilitySeries:
    head: SeriesHead

    # The moment of the dashboard (`data.snapshot_time`), which is more
    # precise than the `timeslot` of the URL: the URL carries the
    # `quote_datetime` that was **requested**.
    snapshot_time: str | None

    # The underlying at the moment of the response (`data.spot`).
    spot_price: float | None
    spot_time: str | None

    rows: list[VolatilityRow]


def parse_volatility(record: dict[str, Any]) -> VolatilitySeries:
    """Interprets a decrypted `volatility-table` response."""
    head = read_head(record, "volatility-table")

    payload = payload_of(record)

    data = payload.get("data")

    if data is None:
        raise StoreError("data absent")

    rows_json = _as_array(_entry_get(data, "data"))

    if rows_json is None:
        raise StoreError("data.data absent")

    header = _as_array(_entry_get(data, "header"))

    if header is None:
        raise StoreError("data.header absent")

    strikes = _volatility_strikes(header)

    rows: list[VolatilityRow] = []

    unknown_columns = 0
    duplicates = 0
    missing = 0
    empty_cells = 0

    seen: set[tuple[str, int]] = set()

    for index, row in enumerate(rows_json):
        obj = _as_object(row)

        if obj is None:
            raise StoreError(f"row {index} is not an object")

        expiry = (_as_str(obj.get("expiry")) or "").strip()

        if not expiry:
            raise StoreError(f"expiry absent at row {index}")

        exp_date = (_as_str(obj.get("exp_date")) or "").strip()

        if not exp_date:
            raise StoreError(f"expiration date absent at row {index}")

        found = 0

        for key, cell in obj.items():
            # `expiry` and `exp_date` are not cells: they are the row.
            if key in ("expiry", "exp_date"):
                continue

            strike_text = strikes.get(key)

            if strike_text is None:
                unknown_columns += 1

                continue

            try:
                strike = float(strike_text)
            except ValueError as error:
                raise StoreError(f"strike '{strike_text}' non-numeric") from error

            if _as_object(cell) is None:
                raise StoreError(f"cell '{key}' is not an object (row {index})")

            # The three fields can be there but be worth `null` (datum not
            # available), or not be there at all: in both cases the cell
            # exists and its row is written.
            iv = _optional_number(cell.get("value"))
            od_value = _optional_number(cell.get("od_value"))
            percentile = _optional_number(cell.get("percentile"))

            if iv is None and od_value is None and percentile is None:
                empty_cells += 1

            found += 1

            if (expiry, strike_key(strike)) in seen:
                duplicates += 1

            seen.add((expiry, strike_key(strike)))

            rows.append(
                VolatilityRow(
                    expiry=expiry,
                    exp_date=exp_date,
                    strike=strike,
                    iv=iv,
                    od_value=od_value,
                    percentile=percentile,
                )
            )

        # The columns declared by the header that this row does not send.
        missing += max(0, len(strikes) - found)

    if unknown_columns > 0:
        raise StoreError(
            f"{unknown_columns} cells with a column that the header does not declare"
        )

    if duplicates > 0:
        raise StoreError(f"{duplicates} repeated cells (same expiry and strike)")

    if not rows:
        raise StoreError("no cell in the response")

    if missing > 0:
        # It is not an error — the grid can have holes — but it is known how
        # full it is, instead of finding out later.
        _info(f"{missing} cells declared by the header are not in the data")

    if empty_cells > 0:
        # Cells that are there but without numbers (the 0DTE with the market
        # closed).
        _info(f"{empty_cells} cells without values (the backend sends `null`)")

    # Trade date and instant: the URL of the volatility does not send them,
    # the payload carries them.
    trade_date = head.trade_date or _as_str(_entry_get(data, "quote_date"))

    if trade_date is None:
        raise StoreError("neither `date` in the URL nor `quote_date` in the payload")

    snapshot_time = _as_str(_entry_get(data, "snapshot_time"))

    timeslot = snapshot_time or head.effective_timeslot()

    # The underlying and the moment of the dashboard are in the payload.
    spot = _entry_get(data, "spot")

    spot_price = _as_f64(_entry_get(spot, "spot_price"))
    spot_time = _as_str(_entry_get(spot, "spot_time"))

    return VolatilitySeries(
        head=replace(head, trade_date=trade_date, timeslot=timeslot),
        snapshot_time=snapshot_time,
        spot_price=spot_price,
        spot_time=spot_time,
        rows=rows,
    )


def _volatility_strikes(header: list[Any]) -> dict[str, str]:
    """The strikes declared by the header: the columns after the first two,
    which are text (`Expiry`, `Exp. Date`).

    It returns the map `title → title`, that is the name of the column as it
    appears **also** in the keys of the data: it is the same textual form,
    and it is the one that is compared.
    """
    strikes: dict[str, str] = {}

    for index, column in enumerate(header):
        title = (_as_str(_entry_get(column, "title")) or "").strip()

        if not title:
            raise StoreError(f"header[{index}]: column without a title")

        # The first two columns are the expiry and its date: they are not
        # strikes, and in the data they do not appear as keys.
        if title.lower() in ("expiry", "exp. date"):
            continue

        try:
            float(title)
        except ValueError as error:
            raise StoreError(f"header[{index}]: '{title}' is not a strike") from error

        if title in strikes:
            raise StoreError(f"header: the column '{title}' is declared twice")

        strikes[title] = title

    if not strikes:
        raise StoreError("header without strikes")

    return strikes


def split_expiration(label: str) -> tuple[str, str]:
    """"2026-10-16, SPX" → ("2026-10-16", "SPX") · "2026-10-16" → ("2026-10-16", "SPXW")

    The API attaches the symbol to the date **only** for the monthly
    expiration that falls on the same day as a weekly one. When it does not
    write it, the expiration is the weekly one: it is what the depth view of
    the same response says, and the verifier checks it again.
    """
    if "," in label:
        date, _, symbol = label.partition(",")

        date = date.strip()
        symbol = symbol.strip()
    else:
        date = label.strip()
        symbol = IMPLICIT_OPTION_SYMBOL

    if not is_iso_date(date):
        raise StoreError(f"expiration '{label}' is not a date")

    if not symbol or not all(character.isascii() and character.isalpha() for character in symbol):
        raise StoreError(f"expiration '{label}' with a symbol that is not a symbol")

    return date, symbol.upper()


def is_iso_date(value: str) -> bool:
    """"2026-10-16" → `True`. The same form that the API uses in the `title`s."""
    raw = value.encode("utf-8", errors="surrogateescape")

    if len(raw) != 10:
        return False

    if raw[4] != ord("-") or raw[7] != ord("-"):
        return False

    return all(
        index in (4, 7) or 0x30 <= byte <= 0x39
        for index, byte in enumerate(raw)
    )


def strike_key(value: float) -> int:
    """The strikes and the prices are compared as numbers, not as text:
    7600 and 7600.0 are the same row."""
    scaled = value * 1_000_000.0

    # The `f64::round()` of the Rust rounds the half **away from zero**;
    # the `round()` of Python does banker's rounding, so it is not used.
    if scaled >= 0:
        rounded = math.floor(scaled + 0.5)
    else:
        rounded = math.ceil(scaled - 0.5)

    return int(rounded)


def _text(record: dict[str, Any], key: str) -> str:
    """A mandatory text field of the decrypted record."""
    value = _as_str(record.get(key))

    if not value:
        raise StoreError(f"{key} absent in the decrypted record")

    return value


# ======================================================
# THE VOLATILITY SURFACE
#
# Three endpoints, two shapes:
#
#   surface      a grid expiration × strike (`z` is as long as `y`, and
#                every row as `x`)
#   companion    the curves of one **smile**: `x` the strikes, `y` the IV
#   term         the curves of one **term structure**: `x` the expirations,
#                `y` the IV, one trace per strike
#
# Trade date and instant are not in the URL (as for the volatility
# table): they are taken from `quote_date` and `snapshot_time` of the
# payload.
# ======================================================


class SliceKind(Enum):
    """Where a slice comes from: from the companion or from the term
    structure."""

    # The smile of one expiration: `x` the strikes.
    SMILE = "smile"

    # The term structure of one strike: `x` the expirations.
    TERM_STRUCTURE = "term-structure"

    def endpoint(self) -> str:
        return {
            SliceKind.SMILE: "volatility-3d-surface-companion",
            SliceKind.TERM_STRUCTURE: "volatility-3d-surface-term-structure",
        }[self]

    def token(self) -> str:
        """The name that ends up in the database."""
        return self.value


@dataclass
class SurfaceRow:
    # The expiration of the `y` axis: "2026-09-30".
    expiration: str

    # The days to expiration, from `dtes` (same length as `y`).
    dte: float | None

    strike: float

    # The IV of the cell.
    iv: float


@dataclass
class SurfaceSeries:
    head: SeriesHead

    snapshot_time: str | None
    spot_price: float | None
    spot_time: str | None

    # The labels of the axes, from the `meta` of the payload.
    x_label: str | None
    y_label: str | None

    # The range declared by the `meta`, as a check: the minimum and the
    # maximum of the values must stay inside it.
    range_min: float | None
    range_max: float | None

    rows: list[SurfaceRow]


def parse_surface(record: dict[str, Any]) -> SurfaceSeries:
    """Interprets a decrypted `volatility-3d-surface` response."""
    head = read_head(record, "volatility-3d-surface")

    payload = payload_of(record)

    data = payload.get("data")

    if data is None:
        raise StoreError("data absent")

    traces = _as_array(_entry_get(_entry_get(data, "figure"), "data"))

    if traces is None:
        raise StoreError("data.figure.data absent")

    if len(traces) != 1:
        raise StoreError(
            f"{len(traces)} traces instead of one: it is not known which one is the surface"
        )

    trace = traces[0]

    x = _array(trace, "x")
    y = _array(trace, "y")

    z = _as_array(_entry_get(trace, "z"))

    if z is None:
        raise StoreError("z absent")

    if len(z) != len(y):
        raise StoreError(
            f"z has {len(z)} rows and y {len(y)} expirations: the grid does not add up"
        )

    # `expirations` repeats the `y`: if it does not, one of the two lies.
    expirations = _as_array(_entry_get(trace, "expirations"))

    if expirations is not None and not _same_json(expirations, y):
        raise StoreError("`expirations` does not repeat the `y`: the grid does not add up")

    # The days to expiration, if the API sends them: one `y` each.
    dtes = _as_array(_entry_get(trace, "dtes"))

    if dtes is not None and len(dtes) != len(y):
        dtes = None

    rows: list[SurfaceRow] = []

    short_rows = 0
    duplicates = 0

    seen: set[tuple[str, int]] = set()

    for index, values in enumerate(z):
        cells = _as_array(values)

        if cells is None:
            raise StoreError(f"z[{index}] is not a row")

        if len(cells) != len(x):
            short_rows += 1

        expiration = (_as_str(_at(y, index)) or "").strip()

        if not expiration:
            raise StoreError(f"non-textual or empty expiration in y[{index}]")

        dte = _as_f64(_at(dtes, index)) if dtes is not None else None

        for column, value in enumerate(cells):
            if column >= len(x):
                continue

            strike = _as_f64(_at(x, column))

            if strike is None:
                raise StoreError(f"non-numeric strike in x[{column}]")

            iv = _as_f64(value)

            if iv is None:
                raise StoreError(f"non-numeric value in z[{index}][{column}]")

            if (expiration, strike_key(strike)) in seen:
                duplicates += 1

            seen.add((expiration, strike_key(strike)))

            rows.append(
                SurfaceRow(expiration=expiration, dte=dte, strike=strike, iv=iv)
            )

    if short_rows > 0:
        raise StoreError(f"{short_rows} rows of z do not have the length of x")

    if duplicates > 0:
        raise StoreError(f"{duplicates} repeated cells (same expiration and strike)")

    if not rows:
        raise StoreError("no cell in the response")

    meta = _entry_get(data, "meta")

    volatility_range = _entry_get(meta, "volatility_range")

    return SurfaceSeries(
        head=_head_with_payload_moment(head, data),
        snapshot_time=_moment_of(data, "snapshot_time"),
        spot_price=_spot_of(data)[0],
        spot_time=_spot_of(data)[1],
        x_label=_as_str(_entry_get(meta, "x_label")),
        y_label=_as_str(_entry_get(meta, "y_label")),
        range_min=_as_f64(_entry_get(volatility_range, "min")),
        range_max=_as_f64(_entry_get(volatility_range, "max")),
        rows=rows,
    )


@dataclass
class SliceRow:
    """A point of a curve."""

    # The point of the curve. For a smile it is a strike, for a term
    # structure it is an expiration.
    expiration: str
    strike: float

    iv: float

    # Which curve: the expiration (smile) or the strike (term structure).
    anchor_expiration: str | None
    anchor_strike: float | None

    # Which trace produced the point: `None` when the curve is a single
    # one (the smile).
    trace_index: int | None


@dataclass
class SliceSeries:
    head: SeriesHead

    kind: SliceKind

    snapshot_time: str | None
    spot_price: float | None
    spot_time: str | None

    rows: list[SliceRow]


def parse_slice(record: dict[str, Any], kind: SliceKind) -> SliceSeries:
    """Interprets a slice response (smile or term structure)."""
    head = read_head(record, kind.endpoint())

    payload = payload_of(record)

    data = payload.get("data")

    if data is None:
        raise StoreError("data absent")

    # The smile has the figure in `data.figure`; the term structure in
    # `data.term.figure`.
    if kind is SliceKind.SMILE:
        figure = _entry_get(data, "figure")
    else:
        figure = _entry_get(_entry_get(data, "term"), "figure")

    if figure is None:
        raise StoreError("the figure of the response is not there")

    traces = _as_array(_entry_get(figure, "data"))

    if traces is None:
        raise StoreError("figure.data absent")

    rows: list[SliceRow] = []

    mismatches = 0
    duplicates = 0

    seen: set[tuple[str, int, int | None]] = set()

    for index, trace in enumerate(traces):
        x = _array(trace, "x")
        y = _array(trace, "y")

        if len(x) != len(y):
            mismatches += 1

            continue

        # The strike of the trace (only for the term structure): the name is
        # "k=5500" and there is also a `label`. If `name` is there but is not
        # a strike, `label` is not looked at: it is what the Rust does, where
        # `or_else` does not fire when the first one is `Some`.
        name = _as_str(_entry_get(trace, "name"))

        if name is None:
            name = _as_str(_entry_get(trace, "label"))

        trace_strike = strike_from_label(name) if name is not None else None

        if kind is SliceKind.TERM_STRUCTURE and trace_strike is None:
            # In Rust the message prints the Debug of the `Option`, which here
            # makes no sense: the name is printed.
            raise StoreError(f"trace '{name}' without a strike in the name")

        # The expiration of the curve (only for the smile): the
        # `series1_label` says it, and the trace is named the same way.
        smile_expiration = (
            _smile_expiration(record, data, trace)
            if kind is SliceKind.SMILE
            else None
        )

        for point, iv_value in enumerate(y):
            iv = _as_f64(iv_value)

            if iv is None:
                raise StoreError(
                    f"non-numeric value in y[{point}] of trace {index}"
                )

            value = _at(x, point)

            if value is None:
                raise StoreError(f"x[{point}] missing in trace {index}")

            # In the smile the points are strikes, in the term structure
            # expirations.
            if kind is SliceKind.SMILE:
                strike = _as_f64(value)

                if strike is None:
                    raise StoreError(
                        f"non-numeric strike in x[{point}] of trace {index}"
                    )

                expiration = smile_expiration or ""
            else:
                expiration = (_as_str(value) or "").strip()

                if not expiration:
                    raise StoreError(
                        f"non-textual expiration in x[{point}] of trace {index}"
                    )

                if trace_strike is None:
                    raise StoreError("strike of the trace absent")

                strike = trace_strike

            trace_index = index if len(traces) > 1 else None

            if (expiration, strike_key(strike), trace_index) in seen:
                duplicates += 1

            seen.add((expiration, strike_key(strike), trace_index))

            rows.append(
                SliceRow(
                    expiration=expiration,
                    strike=strike,
                    iv=iv,
                    anchor_expiration=smile_expiration,
                    anchor_strike=trace_strike,
                    trace_index=trace_index,
                )
            )

    if mismatches > 0:
        raise StoreError(f"{mismatches} traces with x and y of different length")

    if duplicates > 0:
        raise StoreError(f"{duplicates} repeated points in the same curve")

    if not rows:
        raise StoreError("no curve in the response")

    return SliceSeries(
        head=_head_with_payload_moment(head, data),
        kind=kind,
        snapshot_time=_moment_of(data, "snapshot_time"),
        spot_price=_spot_of(data)[0],
        spot_time=_spot_of(data)[1],
        rows=rows,
    )


def strike_from_label(label: str) -> float | None:
    """"k=5500" → 5500. The name of the trace of the term structure."""
    text = label.strip()

    if not text.startswith("k="):
        return None

    try:
        return float(text[2:].strip())
    except ValueError:
        return None


def _smile_expiration(
    record: dict[str, Any], data: Any, trace: Any
) -> str:
    """The expiration of a smile, in the form of a date.

    The payload writes it the human way — `series1_label` is "SPX Sep 30
    2026", and the trace is named the same — while in this column the dates
    are always ISO, as in the other tables. `quote_date` gives the date (it
    is the nearest expiration, the one the dashboard shows); the label is
    kept apart.

    If the two things exist but do not match, it does not guess: the row is
    not written.
    """
    label = (_as_str(_entry_get(data, "series1_label")) or "").strip() or None

    name = (_as_str(_entry_get(trace, "name")) or "").strip() or None

    if label is not None and name is not None and label != name:
        raise StoreError(
            f"the trace is named '{name}' and the series '{label}': "
            "it is not known which one is the expiration"
        )

    date = _moment_of(data, "quote_date")

    if date is None:
        date = query.param(_text_of(record, "url"), "quote_date")

    if date is None:
        raise StoreError(
            "expiration of the smile absent (neither `quote_date` nor the URL)"
        )

    # If the label says the day and the month, the date must be that one:
    # "SPX Sep 30 2026" against "2026-09-30".
    if label is not None:
        found = day_and_month(label)

        if found is not None:
            day, month = found

            if _get_slice(date, 5, 7) != month or _get_slice(date, 8, 10) != day:
                raise StoreError(
                    f"the label '{label}' does not match the date '{date}'"
                )

    return date


def day_and_month(label: str) -> tuple[str, str] | None:
    """"SPX Sep 30 2026" → ("30", "09"): the day and the month, in digits, to
    compare them with an ISO date."""
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]

    parts = label.split()

    for position, part in enumerate(parts):
        if part not in months:
            continue

        month = months.index(part)

        # The day is the word before the month. If the month is the first
        # word there is no day, and in Rust `?` returns from the function
        # at once: it does not keep looking.
        index = position - 1

        if index < 0:
            return None

        day = parts[index]

        if day.isascii() and day.isdigit():
            return day, f"{month + 1:02d}"

    return None


def _head_with_payload_moment(head: SeriesHead, data: Any) -> SeriesHead:
    """Trade date and instant are carried by the payload, not by the URL."""
    trade_date = head.trade_date or _as_str(_entry_get(data, "quote_date"))

    if trade_date is None:
        raise StoreError("neither `date` in the URL nor `quote_date` in the payload")

    timeslot = _moment_of(data, "snapshot_time") or head.effective_timeslot()

    return replace(head, trade_date=trade_date, timeslot=timeslot)


def _moment_of(data: Any, key: str) -> str | None:
    return _as_str(_entry_get(data, key))


def _spot_of(data: Any) -> tuple[float | None, str | None]:
    spot = _entry_get(data, "spot")

    return _as_f64(_entry_get(spot, "spot_price")), _as_str(
        _entry_get(spot, "spot_time")
    )


def _text_of(record: dict[str, Any], key: str) -> str:
    return _as_str(record.get(key)) or ""


# ======================================================
# THE CANDLES (plotly-candlesticks)
#
# One `candlestick` trace named `OHLC`, with `x` the instants and the
# four series `open`, `high`, `low`, `close`: same length, and for
# every candle `high` is the maximum and `low` the minimum (it is checked).
#
# The only thing that this endpoint does differently from all the others is
# **the time zone**: `x` is "2026-10-01T04:00:00-04:00", with the offset
# attached, while elsewhere the times are UTC. Here it is brought to UTC, so
# that `candle_time` is comparable with `timeslot` and `time_slot` of the
# other tables. The original instant stays in the payload, which is in the
# archive.
# ======================================================


@dataclass
class CandleRow:
    # The instant of the candle, **in UTC**: "2026-10-01 08:00:00".
    candle_time: str

    open: float
    high: float
    low: float
    close: float


@dataclass
class CandleSeries:
    head: SeriesHead

    # The unit declared in the URL: `es_futures_point`, `od_score`…
    measurement: str | None

    # The granularity declared in the URL (`1T` = one minute).
    frequency: str | None

    rows: list[CandleRow]


def parse_candles(record: dict[str, Any]) -> CandleSeries:
    """Interprets a decrypted `plotly-candlesticks` response."""
    head = read_head(record, "plotly-candlesticks")

    payload = payload_of(record)

    traces = _figure_traces(payload)

    # The trace of the candles: they are data, and it is recognized by the
    # type.
    grids = [
        trace
        for trace in traces
        if isinstance(trace, dict) and "x" in trace and "close" in trace
    ]

    if not grids:
        raise StoreError("no trace with the candles")

    if len(grids) > 1:
        raise StoreError(
            f"{len(grids)} traces with the candles: it is not known which one is the good one"
        )

    trace = grids[0]

    x = _array(trace, "x")
    opens = _array(trace, "open")
    highs = _array(trace, "high")
    lows = _array(trace, "low")
    closes = _array(trace, "close")

    if not (
        len(opens) == len(x)
        and len(highs) == len(x)
        and len(lows) == len(x)
        and len(closes) == len(x)
    ):
        raise StoreError(
            "the series do not have the same length: "
            f"x {len(x)} · open {len(opens)} · high {len(highs)} · "
            f"low {len(lows)} · close {len(closes)}"
        )

    rows: list[CandleRow] = []

    inconsistent = 0
    duplicates = 0

    seen: set[str] = set()

    for index, instant in enumerate(x):
        raw = _as_str(instant)

        if raw is None:
            raise StoreError(f"non-textual instant in x[{index}]")

        candle_time = utc_moment(raw)

        open_value = _candle_number(_at(opens, index), "open", index)
        high = _candle_number(_at(highs, index), "high", index)
        low = _candle_number(_at(lows, index), "low", index)
        close = _candle_number(_at(closes, index), "close", index)

        # In a candle the maximum is the maximum and the minimum is the
        # minimum: if they are not, the response is not written.
        if (
            high < open_value - 1e-9
            or high < close - 1e-9
            or low > open_value + 1e-9
            or low > close + 1e-9
        ):
            inconsistent += 1

        if candle_time in seen:
            duplicates += 1

        seen.add(candle_time)

        rows.append(
            CandleRow(
                candle_time=candle_time,
                open=open_value,
                high=high,
                low=low,
                close=close,
            )
        )

    if inconsistent > 0:
        raise StoreError(
            f"{inconsistent} candles with high/low that do not contain open/close"
        )

    if duplicates > 0:
        raise StoreError(f"{duplicates} repeated candles (same instant)")

    if not rows:
        raise StoreError("no candle in the response")

    params = query.params(head.url)

    return CandleSeries(
        head=head,
        measurement=params.get("measurement") or None,
        frequency=params.get("frequency") or None,
        rows=rows,
    )


def _candle_number(value: Any, name: str, index: int) -> float:
    """A value of a candle: the four series are all numbers."""
    number = _as_f64(value)

    if number is None:
        raise StoreError(f"{name} non-numeric at candle {index}")

    return number


def utc_moment(value: str) -> str:
    """"2026-10-01T04:00:00-04:00" → "2026-10-01 08:00:00" (UTC).

    The offset is written in the instant, so there is nothing to guess: that
    one is interpreted. An instant without an offset is refused, because
    reading it as UTC would be a guess.
    """
    text = value.strip()

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise StoreError(f"instant '{text}' not interpretable: {error}") from error

    # The `parse_from_rfc3339` of the Rust demands the offset; `fromisoformat`
    # does not, so a naive instant is refused here.
    if parsed.tzinfo is None:
        raise StoreError(
            f"instant '{text}' not interpretable: the time zone offset is missing"
        )

    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ======================================================
# WRITE
# ======================================================


@dataclass
class LoadStats:
    """How a bulk load went (for the report)."""

    records: int = 0
    loaded: int = 0
    skipped: int = 0
    rows: int = 0

    problems: list[str] = field(default_factory=list)


def already_loaded(connection: sqlite3.Connection, response_id: str) -> bool:
    """Is the response already in the database?"""
    row = connection.execute(
        "SELECT id FROM responses WHERE id = ?", (response_id,)
    ).fetchone()

    return row is not None


@dataclass(frozen=True)
class New:
    """Written just now: the response and its rows."""

    rows: int


@dataclass(frozen=True)
class AlreadyThere:
    """It was already in the archive: it is not written twice."""


# What happened to a record that just arrived.
Stored = New | AlreadyThere


def load_file(connection: sqlite3.Connection, path: str) -> LoadStats:
    """Reads a file of decrypted payloads and writes the rows into the database.

    It is not the normal route: the capture writes by itself, while the
    responses arrive. This serves to **recover** what was left behind — an
    old decrypted file, a session loaded when the database was not there.
    It is idempotent, so it can be run whenever one wants.

    The file is read again with the parser of the decryption, not with
    `json` and that is it: the same numbers, read in the same way, or the
    value that ends up in the database is not the one the API sent.
    """
    from actions.decrypt import DecryptError, parse_json

    stats = LoadStats()

    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                record = parse_json(line.encode("utf-8"))
            except DecryptError as error:
                stats.problems.append(f"line {number}: invalid JSON: {error}")

                continue

            if endpoint_of(record) is None:
                # The endpoints that do not have (yet) a table are skipped
                # in silence: they are not an error.
                continue

            stats.records += 1

            try:
                stored = store_record(connection, record)
            except StoreError as error:
                stats.problems.append(f"line {number}: {error}")

                continue

            if isinstance(stored, New):
                stats.loaded += 1
                stats.rows += stored.rows
            else:
                stats.skipped += 1

    return stats


@dataclass(frozen=True)
class EndpointSimple:
    """A table that is recognized from the endpoint."""


@dataclass(frozen=True)
class EndpointSlice:
    """One of the two families of curves: which one needs to be known."""

    kind: SliceKind


# What the database knows how to do with a response.
EndpointKind = EndpointSimple | EndpointSlice


def endpoint_of(record: dict[str, Any]) -> EndpointKind | None:
    """The endpoint of a decrypted response, if the database knows how to
    write it.

    It is **the only list**: both the bulk load and the live capture use it,
    so a new endpoint cannot work on one side and not on the other. An
    endpoint that is not in this list is not an error: it is a dataset that
    is not there yet.
    """
    endpoint = _as_str(record.get("endpoint"))

    # The two families of curves are the same data seen from two sides.
    if endpoint == "volatility-3d-surface-companion":
        return EndpointSlice(kind=SliceKind.SMILE)

    if endpoint == "volatility-3d-surface-term-structure":
        return EndpointSlice(kind=SliceKind.TERM_STRUCTURE)

    if endpoint in (
        "breakdown-by-strike",
        "breakdown-by-expiration",
        "depth-view-table",
        "volatility-table",
        "volatility-3d-surface",
        "plotly-candlesticks",
        "plotly-gamma",
        "plotly-charm",
        "plotly-vanna",
    ):
        return EndpointSimple()

    return None


def store_record(connection: sqlite3.Connection, record: dict[str, Any]) -> Stored:
    """Puts a decrypted response into the database, with the table that is
    its due, in a single transaction.

    Both the bulk load and the live capture use it: it is the same code, so
    the two routes cannot diverge.
    """
    endpoint = _as_str(record.get("endpoint")) or ""

    kind = endpoint_of(record)

    if kind is None:
        raise StoreError("no table for this endpoint")

    response_id = _as_str(record.get("fingerprint"))

    if response_id is None:
        iv = _as_str(record.get("iv")) or ""
        data = _as_str(record.get("data")) or ""
        tag = _as_str(record.get("tag")) or ""

        response_id = fingerprint(iv, data, tag)

    if already_loaded(connection, response_id):
        return AlreadyThere()

    if endpoint == "breakdown-by-strike":
        series = parse_strike(record)

        rows = len(series.rows)

        store(connection, series)
    elif endpoint == "breakdown-by-expiration":
        expiration_series = parse_expiration(record)

        rows = len(expiration_series.rows)

        store_expiration(connection, expiration_series)
    elif endpoint == "depth-view-table":
        depth_series = parse_depth(record)

        rows = len(depth_series.rows)

        store_depth(connection, depth_series)
    elif endpoint == "volatility-table":
        volatility_series = parse_volatility(record)

        rows = len(volatility_series.rows)

        store_volatility(connection, volatility_series)
    elif endpoint == "volatility-3d-surface":
        surface_series = parse_surface(record)

        rows = len(surface_series.rows)

        store_surface(connection, surface_series)
    elif endpoint == "plotly-candlesticks":
        candle_series = parse_candles(record)

        rows = len(candle_series.rows)

        store_candles(connection, candle_series)
    elif isinstance(kind, EndpointSlice):
        # The curves: smile or term structure.
        slice_series = parse_slice(record, kind.kind)

        rows = len(slice_series.rows)

        store_slices(connection, slice_series)
    else:
        # The three heatmaps are the same data in three metrics.
        if endpoint == "plotly-gamma":
            heatmap = HeatmapKind.GAMMA
        elif endpoint == "plotly-charm":
            heatmap = HeatmapKind.CHARM
        else:
            heatmap = HeatmapKind.VANNA

        heatmap_series = parse_heatmap(record, heatmap)

        rows = len(heatmap_series.rows)

        store_heatmap(connection, heatmap_series)

    return New(rows=rows)


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """One transaction per response: either all of it, or nothing.

    In Rust it is `connection.transaction()` of rusqlite. Here `BEGIN`/
    `COMMIT` are said explicitly, because the connection is open in
    autocommit (`isolation_level = None`): it is the only way to have the
    same transaction boundary.
    """
    connection.execute("BEGIN")

    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")

        raise
    else:
        connection.execute("COMMIT")


def _write_row(
    cursor: sqlite3.Cursor,
    statement: str,
    parameters: Sequence[Any],
    message: str = "a row was not written",
) -> None:
    """Runs an INSERT and makes sure that the row really went in.

    In Rust the insert is `INSERT OR IGNORE` and the error is looked at with
    `if let Err(error)`: but `OR IGNORE` **is not an error** — the row
    is skipped in silence, and that check does not notice it. It is the
    defect that had made 81 cells of volatility disappear. With `sqlite3`
    the same thing holds, so the `try` is not enough: `rowcount` is looked
    at too, which says whether the row was written or ignored.
    """
    try:
        cursor.execute(statement, parameters)
    except sqlite3.Error as error:
        raise StoreError(f"{message}: {error}") from error

    if cursor.rowcount == 0:
        raise StoreError(
            f"{message}: the database ignored it "
            "(a constraint of the table is not respected)"
        )


# ======================================================
# KEEPING ONLY THE LATEST SNAPSHOT OF EACH SERIES
#
# The API only ever serves "the latest response of this series" (every
# `/*/latest` route) — so keeping every capture of a trading session on
# disk is pure growth with no one reading the older rows. One capture
# every ~1 minute, all day, across several series per table, is what
# made a single session's `data/od.db` run into gigabytes.
#
# So every write prunes first: before a new response's rows go in, every
# older response of the *same series* — same `exposure`/`side`/
# `expiration_scope` for strikes, same `metric`/`measurement` for
# heatmaps, and so on per table — is deleted from the per-cell table.
# These are the rows that dominate a session's size (one capture every
# ~1 minute, all day, across several series per table — measured: the
# per-cell tables are ~84% of a session's vacuumed size, `responses`
# the rest), so this alone recovers the large majority of the growth.
#
# `responses` — the encrypted archive — is deliberately **not** pruned
# here, even though an old response's row there is, on its own, now
# orphaned (no per-cell row points at it anymore). Two things depend on
# every response staying in `responses` forever:
#
#   · `already_loaded()` is what makes `load` idempotent — it checks
#     `responses` by `id`. Pruning a response's archive row the moment
#     it is superseded made `already_loaded()` forget it, so loading
#     the same capture file twice reprocessed every superseded response
#     a second time instead of skipping it. Measured: 96 of 106
#     responses "rewritten" on a second `load` of the same file, where
#     there should have been zero — `tools/check_no_duplicates.py`
#     caught it immediately.
#   · `tools/verify_store.py` (and `check_fingerprint.py`,
#     `check_no_duplicates.py`) decrypt every row of `responses` to
#     verify the database independently of `store.py`'s own logic —
#     losing the archive row for a superseded response would make that
#     response permanently unverifiable, in a tool whose entire purpose
#     is independent verification.
#
# `responses.data`/`iv`/`tag` (the encrypted payload — the actually
# large columns) staying around for superseded responses is a real,
# accepted cost of keeping both guarantees above cheaply.
#
# `trade_date` is deliberately *not* part of a series' identity below:
# if the daily wipe (`supervisor.wipe_session_data`) is ever skipped —
# a crash, a holiday the supervisor does not know about — the first
# response of a new day still sweeps away whatever the previous day's
# per-cell rows were, instead of accumulating forever. "Only the
# latest" means exactly that, not "only the latest per day".
# ======================================================


def _prune_series(
    connection: sqlite3.Connection,
    table: str,
    identity: dict[str, Any],
    keep_response_id: str,
) -> int:
    """Deletes every response of `table` matching `identity`, other than
    `keep_response_id`. Does not touch `responses` — see the module note
    above for why.

    `IS`, not `=`: a column that is genuinely `NULL` for this series
    (e.g. `measurement` on some candlesticks) must still match — `NULL =
    NULL` is `NULL`, not true, in SQL, and that would silently prune
    nothing instead of the old row it was supposed to replace.

    Returns how many old responses were removed (0 the first time a
    series is seen).
    """
    clauses = " AND ".join(f"{column} IS ?" for column in identity)
    params = list(identity.values())

    stale = [
        row[0]
        for row in connection.execute(
            f"SELECT DISTINCT response_id FROM {table} "  # noqa: S608
            f"WHERE {clauses} AND response_id != ?",
            (*params, keep_response_id),
        ).fetchall()
    ]

    if not stale:
        return 0

    placeholders = ",".join("?" for _ in stale)

    connection.execute(
        f"DELETE FROM {table} WHERE response_id IN ({placeholders})",  # noqa: S608
        stale,
    )

    return len(stale)


def save_response(
    connection: sqlite3.Connection,
    head: SeriesHead,
    exposure: str,
    rows_loaded: int,
) -> None:
    """The archive: one row per response, with the exact encrypted body."""
    connection.execute(
        """
        INSERT INTO responses (
            id, received_at, captured_at, endpoint,
            ticker, trade_date, timeslot, exposure, side,
            rows_loaded, url, iv, data, tag
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            head.response_id,
            head.received_at,
            head.captured_at,
            head.endpoint,
            head.ticker,
            head.require_trade_date(),
            # Can be NULL: some endpoints (the candles) do not send
            # the instant of the dashboard, and inventing it would be worse
            # than leaving it empty.
            head.effective_timeslot(),
            exposure,
            head.side,
            rows_loaded,
            head.url,
            head.iv,
            head.data,
            head.tag,
        ),
    )


def store(connection: sqlite3.Connection, series: StrikeSeries) -> None:
    """Writes a series of strikes: first the response, then its rows, in a
    single transaction (all or nothing)."""
    head = series.head

    exposure = series.exposure

    side = side_of(head)

    with _transaction(connection):
        save_response(connection, head, exposure, len(series.rows))

        _prune_series(
            connection,
            "breakdown_by_strike",
            {
                "ticker": head.ticker,
                "exposure": exposure,
                "side": side,
                "expiration_scope": series.expiration_scope,
            },
            head.response_id,
        )

        cursor = connection.cursor()

        for row in series.rows:
            _write_row(
                cursor,
                """
                INSERT OR IGNORE INTO breakdown_by_strike (
                    response_id, received_at, timeslot, trade_date, ticker,
                    exposure, side, expiration_scope, strike, call, put, net
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    head.response_id,
                    head.received_at,
                    head.effective_timeslot(),
                    head.trade_date,
                    head.ticker,
                    exposure,
                    side,
                    series.expiration_scope,
                    row.strike,
                    row.call,
                    row.put,
                    row.net,
                ),
            )

        cursor.close()


def store_expiration(
    connection: sqlite3.Connection, series: ExpirationSeries
) -> None:
    """Writes a series of expirations: one row per expiration."""
    head = series.head

    exposure = series.exposure

    side = side_of(head)

    with _transaction(connection):
        save_response(connection, head, exposure, len(series.rows))

        _prune_series(
            connection,
            "breakdown_by_expiration",
            {"ticker": head.ticker, "exposure": exposure, "side": side},
            head.response_id,
        )

        cursor = connection.cursor()

        for row in series.rows:
            _write_row(
                cursor,
                """
                INSERT OR IGNORE INTO breakdown_by_expiration (
                    response_id, received_at, timeslot, trade_date, ticker,
                    exposure, side, expiration, option_symbol, call, put, net
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    head.response_id,
                    head.received_at,
                    head.effective_timeslot(),
                    head.trade_date,
                    head.ticker,
                    exposure,
                    side,
                    row.expiration,
                    row.option_symbol,
                    row.call,
                    row.put,
                    row.net,
                ),
            )

        cursor.close()


def store_heatmap(connection: sqlite3.Connection, series: HeatmapSeries) -> None:
    """Writes a heatmap: one row per instant × price."""
    head = series.head

    with _transaction(connection):
        save_response(connection, head, series.metric, len(series.rows))

        _prune_series(
            connection,
            "heatmaps",
            {"ticker": head.ticker, "metric": series.metric, "measurement": series.measurement},
            head.response_id,
        )

        cursor = connection.cursor()

        for row in series.rows:
            _write_row(
                cursor,
                """
                INSERT OR IGNORE INTO heatmaps (
                    response_id, received_at, timeslot, trade_date, ticker,
                    metric, measurement, time_slot, price, value,
                    color_scale_min, color_scale_max
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    head.response_id,
                    head.received_at,
                    head.effective_timeslot(),
                    head.trade_date,
                    head.ticker,
                    series.metric,
                    series.measurement,
                    row.time_slot,
                    row.price,
                    row.value,
                    series.color_scale_min,
                    series.color_scale_max,
                ),
            )

        cursor.close()


def store_candles(connection: sqlite3.Connection, series: CandleSeries) -> None:
    """Writes the candles: one row per candle."""
    head = series.head

    with _transaction(connection):
        save_response(connection, head, "CANDLES", len(series.rows))

        _prune_series(
            connection,
            "candlesticks",
            {"ticker": head.ticker, "measurement": series.measurement, "frequency": series.frequency},
            head.response_id,
        )

        cursor = connection.cursor()

        for row in series.rows:
            _write_row(
                cursor,
                """
                INSERT OR IGNORE INTO candlesticks (
                    response_id, received_at, trade_date, ticker,
                    measurement, frequency, candle_time, open, high, low, close
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    head.response_id,
                    head.received_at,
                    head.require_trade_date(),
                    head.ticker,
                    series.measurement,
                    series.frequency,
                    row.candle_time,
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                ),
            )

        cursor.close()


def store_surface(connection: sqlite3.Connection, series: SurfaceSeries) -> None:
    """Writes the surface: one row per expiration × strike."""
    head = series.head

    with _transaction(connection):
        save_response(connection, head, "SURFACE", len(series.rows))

        _prune_series(
            connection, "volatility_surface", {"ticker": head.ticker}, head.response_id
        )

        cursor = connection.cursor()

        for row in series.rows:
            _write_row(
                cursor,
                """
                INSERT OR IGNORE INTO volatility_surface (
                    response_id, received_at, timeslot, trade_date, ticker,
                    snapshot_time, spot_price, spot_time,
                    expiration, dte, strike, iv,
                    x_label, y_label, range_min, range_max
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    head.response_id,
                    head.received_at,
                    # Can be `None`: a response may not carry
                    # the instant. The row is written all the same — the
                    # column accepts it — instead of losing the datum.
                    head.effective_timeslot(),
                    head.require_trade_date(),
                    head.ticker,
                    series.snapshot_time,
                    series.spot_price,
                    series.spot_time,
                    row.expiration,
                    row.dte,
                    row.strike,
                    row.iv,
                    series.x_label,
                    series.y_label,
                    series.range_min,
                    series.range_max,
                ),
            )

        cursor.close()


def store_slices(connection: sqlite3.Connection, series: SliceSeries) -> None:
    """Writes the curves: one row per point of a curve."""
    head = series.head

    exposure = "SMILE" if series.kind is SliceKind.SMILE else "TERM"

    with _transaction(connection):
        save_response(connection, head, exposure, len(series.rows))

        _prune_series(
            connection,
            "volatility_slices",
            {"ticker": head.ticker, "kind": series.kind.token()},
            head.response_id,
        )

        cursor = connection.cursor()

        for row in series.rows:
            _write_row(
                cursor,
                """
                INSERT OR IGNORE INTO volatility_slices (
                    response_id, received_at, timeslot, trade_date, ticker,
                    snapshot_time, spot_price, spot_time,
                    kind, trace_index, anchor_expiration, anchor_strike,
                    expiration, strike, iv
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    head.response_id,
                    head.received_at,
                    # Can be `None`: a response may not carry
                    # the instant. The row is written all the same — the
                    # column accepts it — instead of losing the datum.
                    head.effective_timeslot(),
                    head.require_trade_date(),
                    head.ticker,
                    series.snapshot_time,
                    series.spot_price,
                    series.spot_time,
                    series.kind.token(),
                    row.trace_index,
                    row.anchor_expiration,
                    row.anchor_strike,
                    row.expiration,
                    row.strike,
                    row.iv,
                ),
            )

        cursor.close()


def store_volatility(
    connection: sqlite3.Connection, series: VolatilitySeries
) -> None:
    """Writes a series of volatility: one row per expiry × strike."""
    head = series.head

    with _transaction(connection):
        save_response(connection, head, "VOLATILITY", len(series.rows))

        _prune_series(
            connection, "volatility_table", {"ticker": head.ticker}, head.response_id
        )

        cursor = connection.cursor()

        for row in series.rows:
            # `OR IGNORE` would skip in silence: if a row is
            # discarded, one wants to know why.
            _write_row(
                cursor,
                """
                INSERT OR IGNORE INTO volatility_table (
                    response_id, received_at, timeslot, trade_date, ticker,
                    snapshot_time, spot_price, spot_time,
                    expiry, exp_date, strike, iv, od_value, percentile
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    head.response_id,
                    head.received_at,
                    head.effective_timeslot(),
                    head.trade_date,
                    head.ticker,
                    series.snapshot_time,
                    series.spot_price,
                    series.spot_time,
                    row.expiry,
                    row.exp_date,
                    row.strike,
                    row.iv,
                    row.od_value,
                    row.percentile,
                ),
                f"cell {row.expiry} · {row.exp_date} · "
                f"{_display_number(row.strike)} not written",
            )

        cursor.close()


def store_depth(connection: sqlite3.Connection, series: DepthSeries) -> None:
    """Writes a series of depth: one row per strike × expiration."""
    head = series.head

    exposure = series.exposure

    side = side_of(head)

    with _transaction(connection):
        save_response(connection, head, exposure, len(series.rows))

        _prune_series(
            connection,
            "depth_view",
            {"ticker": head.ticker, "exposure": exposure, "side": side},
            head.response_id,
        )

        cursor = connection.cursor()

        for row in series.rows:
            _write_row(
                cursor,
                """
                INSERT OR IGNORE INTO depth_view (
                    response_id, received_at, timeslot, trade_date, ticker,
                    exposure, side, strike, expiration, option_symbol,
                    call, put, net
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    head.response_id,
                    head.received_at,
                    head.effective_timeslot(),
                    head.trade_date,
                    head.ticker,
                    exposure,
                    side,
                    row.strike,
                    row.expiration,
                    row.option_symbol,
                    row.call,
                    row.put,
                    row.net,
                ),
            )

        cursor.close()


# ======================================================
# READ (for the reports and the checks)
# ======================================================


@dataclass
class Coverage:
    """What there is, per series: the two breakdowns and the depth view."""

    exposure: str
    side: str
    trade_date: str
    slots: int
    rows: int

    # How many distinct expirations (0 for the breakdown by strike, which
    # has no expirations on the rows).
    expirations: int

    # How many distinct expirations with the same symbol (0 where it does
    # not apply): it says how many rows are the same date twice —
    # monthly and weekly on the same day.
    expiration_symbols: int

    # Which expirations the series was aggregated over (`expiration_scope_of`):
    # "ALL", "0DTE", or a literal range. Empty for the tables that do not
    # carry it (the two breakdowns by expiration/depth already say the
    # expiration on every row, so the question does not apply to them).
    expiration_scope: str


def _coverage_rows(connection: sqlite3.Connection, sql: str) -> list[Coverage]:
    return [
        Coverage(
            exposure=row[0],
            side=row[1],
            trade_date=row[2],
            slots=row[3],
            rows=row[4],
            expirations=row[5],
            expiration_symbols=row[6],
            expiration_scope=row[7],
        )
        for row in connection.execute(sql).fetchall()
    ]


def coverage(connection: sqlite3.Connection) -> list[Coverage]:
    """What there is, per series: breakdown by strike.

    Grouped by `expiration_scope` too: "Strikes" (0DTE) and "StrikeAll"
    (ALL) hit the same endpoint and must not be merged into one row.
    """
    return _coverage_rows(
        connection,
        """
        SELECT exposure, side, trade_date,
               COUNT(DISTINCT timeslot) AS slots,
               COUNT(*) AS rows,
               0 AS expirations,
               0 AS expiration_symbols,
               expiration_scope
        FROM breakdown_by_strike
        GROUP BY exposure, side, trade_date, expiration_scope
        ORDER BY trade_date, exposure, side, expiration_scope
        """,
    )


def expiration_coverage(connection: sqlite3.Connection) -> list[Coverage]:
    """What there is, per series: breakdown by expiration (one row per
    expiration).

    `expirations` counts the distinct dates, `expiration_symbols` the
    distinct dates per symbol: if the two numbers differ, somewhere a date
    is two expirations (monthly + weekly).
    """
    return _coverage_rows(
        connection,
        """
        SELECT exposure, side, trade_date,
               COUNT(DISTINCT timeslot) AS slots,
               COUNT(*) AS rows,
               COUNT(DISTINCT expiration) AS expirations,
               COUNT(DISTINCT expiration || '|' || option_symbol) AS expiration_symbols,
               '' AS expiration_scope
        FROM breakdown_by_expiration
        GROUP BY exposure, side, trade_date
        ORDER BY trade_date, exposure, side
        """,
    )


def depth_coverage(connection: sqlite3.Connection) -> list[Coverage]:
    """What there is, per series: depth view (strike × expiration)."""
    return _coverage_rows(
        connection,
        """
        SELECT exposure, side, trade_date,
               COUNT(DISTINCT timeslot) AS slots,
               COUNT(*) AS rows,
               COUNT(DISTINCT expiration) AS expirations,
               COUNT(DISTINCT expiration || '|' || option_symbol) AS expiration_symbols,
               '' AS expiration_scope
        FROM depth_view
        GROUP BY exposure, side, trade_date
        ORDER BY trade_date, exposure, side
        """,
    )


@dataclass
class HeatmapCoverage:
    """What there is, per series: heatmap.

    The grid has two axes (instants × prices) like the depth view, but here
    the series is a metric: two numbers are enough, and they are called by
    name.
    """

    metric: str

    # The unit declared in the URL (`es_futures_point`, `od_score`…):
    # it says what the values are in.
    measurement: str

    trade_date: str

    # How many distinct instants (the responses) and how many distinct
    # prices (the rows of the grid).
    slots: int
    prices: int

    rows: int


def heatmap_coverage(connection: sqlite3.Connection) -> list[HeatmapCoverage]:
    """What there is, per series: heatmap (one row per instant × price)."""
    return [
        HeatmapCoverage(
            metric=row[0],
            measurement=row[1],
            trade_date=row[2],
            slots=row[3],
            prices=row[4],
            rows=row[5],
        )
        for row in connection.execute(
            """
            SELECT metric, COALESCE(measurement, ''), trade_date,
                   COUNT(DISTINCT timeslot) AS slots,
                   COUNT(DISTINCT price) AS prices,
                   COUNT(*) AS rows
            FROM heatmaps
            GROUP BY metric, measurement, trade_date
            ORDER BY trade_date, metric
            """
        ).fetchall()
    ]


@dataclass
class VolatilityCoverage:
    """What there is, per series: volatility table (expiry × strike).

    The series is the response: the instants are the `timeslot`s and the
    "prices" are the distinct expiries, that is the rows of the grid.
    """

    trade_date: str
    timeslot: str

    tenors: int
    strikes: int

    rows: int


def volatility_coverage(
    connection: sqlite3.Connection,
) -> list[VolatilityCoverage]:
    return [
        VolatilityCoverage(
            trade_date=row[0],
            timeslot=row[1],
            tenors=row[2],
            strikes=row[3],
            rows=row[4],
        )
        for row in connection.execute(
            """
            SELECT trade_date, timeslot,
                   COUNT(DISTINCT expiry) AS tenors,
                   COUNT(DISTINCT strike) AS strikes,
                   COUNT(*) AS rows
            FROM volatility_table
            GROUP BY trade_date, timeslot
            ORDER BY trade_date, timeslot
            """
        ).fetchall()
    ]


@dataclass
class SurfaceCoverage:
    """What there is, per series: volatility surface."""

    trade_date: str
    timeslot: str

    expirations: int
    strikes: int

    rows: int


def surface_coverage(connection: sqlite3.Connection) -> list[SurfaceCoverage]:
    return [
        SurfaceCoverage(
            trade_date=row[0],
            timeslot=row[1],
            expirations=row[2],
            strikes=row[3],
            rows=row[4],
        )
        for row in connection.execute(
            """
            SELECT trade_date, timeslot,
                   COUNT(DISTINCT expiration) AS expirations,
                   COUNT(DISTINCT strike) AS strikes,
                   COUNT(*) AS rows
            FROM volatility_surface
            GROUP BY trade_date, timeslot
            ORDER BY trade_date, timeslot
            """
        ).fetchall()
    ]


@dataclass
class SliceCoverage:
    """What there is, per series: the curves of the surface.

    One row per response (not per instant): two captures can refer to the
    same `snapshot_time`, and the points are of different responses.
    """

    received_at: str

    kind: str
    timeslot: str

    # How many distinct curves (expirations for the smile, strikes for the
    # term structure) and how many points in all.
    curves: int
    points: int


def slice_coverage(connection: sqlite3.Connection) -> list[SliceCoverage]:
    return [
        SliceCoverage(
            received_at=row[0],
            kind=row[1],
            timeslot=row[2],
            curves=row[3],
            points=row[4],
        )
        for row in connection.execute(
            """
            SELECT received_at, kind, timeslot,
                   COUNT(DISTINCT COALESCE(anchor_expiration, 'k=' || anchor_strike)) AS curves,
                   COUNT(*) AS points
            FROM volatility_slices
            GROUP BY response_id, received_at, kind, timeslot
            ORDER BY kind, timeslot, received_at
            """
        ).fetchall()
    ]


@dataclass
class CandleCoverage:
    """What there is, per series: the candles.

    The series is a response: `trade_date`, `measurement` and `frequency`
    tell it apart, because the same trade date arrives once per unit of
    measurement.

    `from_time`/`to_time` are the first and the last instant, in UTC: in
    Rust they are called `from` and `to`, which in Python are reserved
    words.
    """

    trade_date: str
    measurement: str
    frequency: str

    candles: int

    from_time: str
    to_time: str


def candle_coverage(connection: sqlite3.Connection) -> list[CandleCoverage]:
    return [
        CandleCoverage(
            trade_date=row[0],
            measurement=row[1],
            frequency=row[2],
            candles=row[3],
            from_time=row[4],
            to_time=row[5],
        )
        for row in connection.execute(
            """
            SELECT trade_date, COALESCE(measurement, ''), COALESCE(frequency, ''),
                   COUNT(*) AS candles,
                   MIN(candle_time) AS from_time, MAX(candle_time) AS to_time
            FROM candlesticks
            GROUP BY trade_date, measurement, frequency
            ORDER BY trade_date, measurement
            """
        ).fetchall()
    ]


def response_count(connection: sqlite3.Connection) -> int:
    """How many responses are in the archive (used by the report of the
    database)."""
    return connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0]


def row_count(connection: sqlite3.Connection) -> int:
    """How many rows of strikes are in the archive."""
    return connection.execute(
        "SELECT COUNT(*) FROM breakdown_by_strike"
    ).fetchone()[0]


def expiration_row_count(connection: sqlite3.Connection) -> int:
    """How many rows of expirations are in the archive."""
    return connection.execute(
        "SELECT COUNT(*) FROM breakdown_by_expiration"
    ).fetchone()[0]


def depth_row_count(connection: sqlite3.Connection) -> int:
    """How many cells of depth are in the archive."""
    return connection.execute("SELECT COUNT(*) FROM depth_view").fetchone()[0]


# ======================================================
# HELPERS
# ======================================================


def _required(params: dict[str, str], key: str) -> str:
    value = params.get(key)

    if not value:
        raise StoreError(f"parameter {key} absent in the URL")

    return value


def _trace(traces: list[Any], name: str) -> dict[str, Any]:
    for trace in traces:
        if isinstance(trace, dict) and trace.get("name") == name:
            return trace

    raise StoreError(f"trace '{name}' absent")


def _figure_traces(payload: Any) -> list[Any]:
    """The traces of a Plotly figure: `figure.data[]`."""
    traces = _as_array(_entry_get(_entry_get(payload, "figure"), "data"))

    if traces is None:
        raise StoreError("figure.data absent")

    return traces


def _array(trace: Any, key: str) -> list[Any]:
    values = _as_array(_entry_get(trace, key))

    if values is None:
        raise StoreError(f"{key} absent")

    return values


def _number(value: Any) -> float:
    number = _as_f64(value)

    if number is None:
        raise StoreError("non-numeric value")

    return number


def _optional_number(value: Any) -> float | None:
    """A number that may not be there, or be there but be worth `null`: in
    both cases it is not an error, it is a datum that the API does not
    have."""
    return _as_f64(value)


def _entry_get(entry: Any, key: str) -> Any:
    """`entry.get(key)` when `entry` may not be an object.

    In Rust one writes `value.get(key)` on any `Value` and gets `None`; here
    one more step is needed, because `None` and a list do not have `.get`.
    """
    if isinstance(entry, dict):
        return entry.get(key)

    return None


def _get_slice(value: str, start: int, end: int) -> str | None:
    """The `value.get(start..end)` of the Rust: `None` if the slice is not
    there.

    In Python `"abc"[5:7]` is `""`, which is not the same thing as "out of
    range".
    """
    if end > len(value):
        return None

    return value[start:end]


def _info(message: str) -> None:
    """An informational note on the console, like `eprintln!("   ℹ️ …")`.

    The character is not ASCII, and on Windows the console is often cp1252:
    writing the UTF-8 **bytes** on the buffer is what the Rust does, and it
    does not stop with `UnicodeEncodeError` the way `print` would (which
    goes through the encoding of the console).
    """
    line = f"   ℹ️ {message}"

    try:
        print(line, file=sys.stderr)
    except UnicodeEncodeError:
        buffer = getattr(sys.stderr, "buffer", None)

        if buffer is None:
            print(line.encode("utf-8", errors="replace").decode("ascii", errors="replace"),
                  file=sys.stderr)

            return

        buffer.write((line + "\n").encode("utf-8"))
        buffer.flush()
