"""The OptionsDepth data API: the latest snapshot of each series.

    uvicorn server.main:app --host 127.0.0.1 --port 8000

Every route below `/health` requires the `X-API-Key` header (checked
against `OD_API_KEY`, see `server/config.py`). The data itself comes
straight from `data/od.db`, read-only — this process never writes to
the archive, the scraper (`python main.py`) is the only writer.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status

# `src/store.py` already knows how to summarize what is in the database
# (the `*_coverage` functions behind `python main.py load`'s report): the
# `/series` endpoint reuses it instead of re-deriving the same GROUP BYs.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import store as store_module  # noqa: E402

from server.auth import require_api_key  # noqa: E402
from server.config import settings  # noqa: E402
from server.db import connection_dependency  # noqa: E402
from server.queries import SeriesNotFound, latest_series  # noqa: E402

app = FastAPI(
    title="OptionsDepth Data API",
    description="Read-only access to the data captured by the OD scraper.",
    version="1.0.0",
)

Connection = Annotated[sqlite3.Connection, Depends(connection_dependency)]
Authorized = Depends(require_api_key)


@app.get("/health")
def health() -> dict:
    """No auth: just says whether the database is there."""
    path = Path(settings.database)

    return {"status": "ok", "database": str(path.resolve()), "exists": path.exists()}


def _latest_or_404(connection: sqlite3.Connection, table: str, filters: dict[str, str | None]) -> dict:
    try:
        return latest_series(connection, table, filters)
    except SeriesNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


# ======================================================
# SERIES OVERVIEW — what is actually in the database
# ======================================================


@app.get("/series", dependencies=[Authorized])
def series_overview(connection: Connection) -> dict:
    """Coverage per table: which (exposure, side, trade_date, …) exist.

    Meant to discover which filter values are valid before calling one
    of the `/*/latest` routes below.
    """
    return {
        "strikes": [asdict(row) for row in store_module.coverage(connection)],
        "expirations": [asdict(row) for row in store_module.expiration_coverage(connection)],
        "depth": [asdict(row) for row in store_module.depth_coverage(connection)],
        "heatmaps": [asdict(row) for row in store_module.heatmap_coverage(connection)],
        "volatility_table": [asdict(row) for row in store_module.volatility_coverage(connection)],
        "volatility_surface": [asdict(row) for row in store_module.surface_coverage(connection)],
        "volatility_slices": [asdict(row) for row in store_module.slice_coverage(connection)],
        "candles": [asdict(row) for row in store_module.candle_coverage(connection)],
    }


# ======================================================
# LATEST SNAPSHOT, PER TABLE
# ======================================================


@app.get("/strikes/latest", dependencies=[Authorized])
def strikes_latest(
    connection: Connection,
    exposure: str | None = None,
    side: str | None = None,
    ticker: str | None = None,
    trade_date: str | None = None,
    expiration_scope: str | None = None,
) -> dict:
    """`expiration_scope`: `ALL` (every expiration), `0DTE`, or a literal
    `{start}..{end}` range — see `store.expiration_scope_of`. Needed
    because two dashboard widgets (e.g. "Strikes" and "StrikeAll") can
    hit this same endpoint and differ only by this."""
    return _latest_or_404(
        connection,
        "breakdown_by_strike",
        {
            "exposure": exposure,
            "side": side,
            "ticker": ticker,
            "trade_date": trade_date,
            "expiration_scope": expiration_scope,
        },
    )


@app.get("/expirations/latest", dependencies=[Authorized])
def expirations_latest(
    connection: Connection,
    exposure: str | None = None,
    side: str | None = None,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict:
    return _latest_or_404(
        connection,
        "breakdown_by_expiration",
        {"exposure": exposure, "side": side, "ticker": ticker, "trade_date": trade_date},
    )


@app.get("/depth/latest", dependencies=[Authorized])
def depth_latest(
    connection: Connection,
    exposure: str | None = None,
    side: str | None = None,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict:
    return _latest_or_404(
        connection,
        "depth_view",
        {"exposure": exposure, "side": side, "ticker": ticker, "trade_date": trade_date},
    )


@app.get("/heatmaps/latest", dependencies=[Authorized])
def heatmaps_latest(
    connection: Connection,
    metric: str | None = None,
    measurement: str | None = None,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict:
    """`metric`: GAMMA, CHARM or VANNA. Without it, the latest of any metric."""
    return _latest_or_404(
        connection,
        "heatmaps",
        {"metric": metric, "measurement": measurement, "ticker": ticker, "trade_date": trade_date},
    )


@app.get("/volatility/table/latest", dependencies=[Authorized])
def volatility_table_latest(
    connection: Connection,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict:
    return _latest_or_404(
        connection,
        "volatility_table",
        {"ticker": ticker, "trade_date": trade_date},
    )


@app.get("/volatility/surface/latest", dependencies=[Authorized])
def volatility_surface_latest(
    connection: Connection,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict:
    return _latest_or_404(
        connection,
        "volatility_surface",
        {"ticker": ticker, "trade_date": trade_date},
    )


@app.get("/volatility/slices/latest", dependencies=[Authorized])
def volatility_slices_latest(
    connection: Connection,
    kind: str | None = None,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict:
    """`kind`: `smile` or `term-structure`."""
    return _latest_or_404(
        connection,
        "volatility_slices",
        {"kind": kind, "ticker": ticker, "trade_date": trade_date},
    )


@app.get("/candles/latest", dependencies=[Authorized])
def candles_latest(
    connection: Connection,
    measurement: str | None = None,
    frequency: str | None = None,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict:
    return _latest_or_404(
        connection,
        "candlesticks",
        {"measurement": measurement, "frequency": frequency, "ticker": ticker, "trade_date": trade_date},
    )
