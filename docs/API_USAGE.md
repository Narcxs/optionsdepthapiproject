# OptionsDepth Data API

Reference for calling `odapi.qtmarketdata.com` from a client application.
**Contains a live Bearer token** (below) — do not share this file or
publish it anywhere public.

## Base URL

```
https://odapi.qtmarketdata.com
```

Served over HTTPS (Let's Encrypt) via a reverse proxy in front of the
actual API process, which never listens on anything but localhost.

## Authentication

Every route except `/health` requires this header on each request:

```
Authorization: Bearer <token>
```

### Token

```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJvZC1hcGkiLCJzdWIiOiJ0ZXN0IiwianRpIjoiMmZhZmY5MmNlYmJjNGE3NzVlMTVhYzU3NWI1MTNkNmQiLCJpYXQiOjE3OTMwMjQwODcsImV4cCI6MTc5MzYyODg4N30.d9DuLNXMwwCF-JqhZmGFoVibRwJtY7pnag0Y59ZVx7A
```

This token expires automatically — no action needed when it does; a
new one replaces it on request. Until then, every request below uses it
as-is.

### Authentication errors

| Code | Cause |
|---|---|
| `401` | Missing `Authorization` header |
| `401` | Invalid signature (wrong secret, tampered token) |
| `401` | Token expired |
| `401` | Token revoked, or not recognized |

## `GET /health` — no authentication required

```json
{"status": "ok", "database": "<absolute path on the server>", "exists": true}
```

Confirms the server is up and whether its database file currently
exists. `exists: false` means the database was cleared between trading
sessions and has not been recreated by a new capture yet — not an error
on its own.

## `GET /series` — what data is currently available

Call this first to discover which filter values are valid before
calling one of the `/*/latest` routes below. Returns a coverage summary
per table: which `(exposure, side, trade_date, …)` combinations exist
right now, and how many rows each one holds.

```json
{
  "strikes": [
    {"exposure": "GEX", "side": "MM", "trade_date": "2026-10-26", "slots": 1, "rows": 239, "expirations": 0, "expiration_symbols": 0, "expiration_scope": "0DTE"}
  ],
  "expirations": [...],
  "depth": [...],
  "heatmaps": [
    {"metric": "GAMMA", "measurement": "es_futures_point", "trade_date": "2026-10-26", "slots": 1, "prices": 92, "rows": 7820}
  ],
  "volatility_table": [
    {"trade_date": "2026-10-26", "timeslot": "2026-10-26 10:16:06", "tenors": 31, "strikes": 81, "rows": 2511}
  ],
  "volatility_surface": [...],
  "volatility_slices": [
    {"received_at": "2026-10-26 14:17:53", "kind": "smile", "timeslot": "2026-10-26 10:15:17", "curves": 1, "points": 41}
  ],
  "candles": [
    {"trade_date": "2026-10-26", "measurement": "es_futures_point", "frequency": "1T", "candles": 374, "from_time": "2026-10-26 08:00:00", "to_time": "2026-10-26 14:13:00"}
  ]
}
```

`slots` is how many distinct instants were captured for that
combination; `rows` is the total cell count. Since only the latest
capture of each series is kept (older ones are pruned automatically),
`slots` is almost always `1`.

## The `/*/latest` routes

All of them share the same response envelope:

```json
{
  "response_id": "9d93b39962bcb998",
  "trade_date": "2026-10-26",
  "timeslot": "2026-10-26 10:16:00",
  "received_at": "2026-10-26 14:17:53",
  "count": 239,
  "rows": [ { ... }, { ... } ]
}
```

| Field | Meaning |
|---|---|
| `response_id` | unique fingerprint of the captured response the rows came from |
| `trade_date` | trading day the data is about |
| `timeslot` | the dashboard's instant at capture time |
| `received_at` | when the scraper received this response (UTC) |
| `count` | number of entries in `rows` |
| `rows` | the actual data — columns documented per endpoint below |

If no row matches the given filters, the response is `404` with a
detail like `"No data in 'breakdown_by_strike' for the given filters"`.
With no filters at all, the single most recent response of any kind.

---

### `GET /strikes/latest`

Exposure (delta/gamma/vega/charm) broken down by strike, for one
expiration or aggregated across all of them (see
`docs/endpoints/breakdown-by-strike.md` for the full story behind the
two source dashboards, "Strikes" and "StrikeAll").

| Parameter | Type | Values |
|---|---|---|
| `exposure` | string | `GEX`, `VEX`, `DEX`, `CEX`, `NET_POSITION` |
| `side` | string | `MM`, `CUSTOMER` |
| `ticker` | string | e.g. `SPX` |
| `trade_date` | string | `YYYY-MM-DD` |
| `expiration_scope` | string | `0DTE`, `ALL`, or a literal `{start}..{end}` range |

`rows[]` columns:

| Column | Type | Description |
|---|---|---|
| `response_id` | string | see above |
| `received_at`, `timeslot`, `trade_date`, `ticker` | string | time context |
| `exposure`, `side`, `expiration_scope` | string | identity of the series |
| `strike` | number | the strike price |
| `call` | number \| null | exposure from call options at this strike |
| `put` | number \| null | exposure from put options at this strike |
| `net` | number \| null | `call + put` — the headline number |

Example:
```
curl -H "Authorization: Bearer $TOKEN" \
  "https://odapi.qtmarketdata.com/strikes/latest?exposure=GEX&side=MM&expiration_scope=0DTE"
```

---

### `GET /expirations/latest`

The same exposure metrics as `/strikes/latest`, aggregated by
expiration instead of by strike.

| Parameter | Type | Values |
|---|---|---|
| `exposure` | string | `GEX`, `VEX`, `DEX`, `CEX`, `NET_POSITION` |
| `side` | string | `MM`, `CUSTOMER` |
| `ticker` | string | e.g. `SPX` |
| `trade_date` | string | `YYYY-MM-DD` |

`rows[]` columns:

| Column | Description |
|---|---|
| `response_id`, `received_at`, `timeslot`, `trade_date`, `ticker` | context |
| `exposure`, `side` | identity of the series |
| `expiration` | the expiration date (`YYYY-MM-DD`) |
| `option_symbol` | `SPX` (monthly) or `SPXW` (weekly) — distinguishes two expirations falling on the same date |
| `call`, `put`, `net` | exposure for that expiration |

---

### `GET /depth/latest`

The full strike × expiration grid — the most granular view, often
several thousand rows.

| Parameter | Type | Values |
|---|---|---|
| `exposure` | string | `GEX`, `VEX`, `DEX`, `CEX`, `NET_POSITION` |
| `side` | string | `MM`, `CUSTOMER` |
| `ticker` | string | e.g. `SPX` |
| `trade_date` | string | `YYYY-MM-DD` |

`rows[]` columns:

| Column | Description |
|---|---|
| `response_id`, `received_at`, `timeslot`, `trade_date`, `ticker` | context |
| `exposure`, `side` | identity of the series |
| `strike` | the strike price |
| `expiration`, `option_symbol` | same meaning as in `/expirations/latest` |
| `call`, `put`, `net` | exposure for that cell (strike × expiration) |

A cell missing from the source response has **no row** here at all —
that is an absence of data, not a zero value.

---

### `GET /heatmaps/latest`

The gamma/charm/vanna grid: one value per (instant × underlying price),
across the whole trading day (see `docs/endpoints/plotly-gamma.md` for
the full breakdown of the underlying payload).

| Parameter | Type | Values |
|---|---|---|
| `metric` | string | `GAMMA`, `CHARM`, `VANNA` |
| `measurement` | string | unit the dashboard requested (e.g. `es_futures_point`, `es_futures_5_min`, `od_score`) |
| `ticker` | string | e.g. `SPX` |
| `trade_date` | string | `YYYY-MM-DD` |

`rows[]` columns:

| Column | Description |
|---|---|
| `response_id`, `received_at`, `timeslot`, `trade_date`, `ticker` | context |
| `metric`, `measurement` | identity of the series |
| `time_slot` | the grid's instant (`YYYY-MM-DD HH:MM:SS`) |
| `price` | underlying price on this axis |
| `value` | the cell's value (gamma/charm/vanna) |
| `color_scale_min`, `color_scale_max` | the requested color-scale bounds — display only, does not clamp `value` |

---

### `GET /volatility/table/latest`

The implied-volatility table: one row per (tenor × strike).

| Parameter | Type | Values |
|---|---|---|
| `ticker` | string | e.g. `SPX` |
| `trade_date` | string | `YYYY-MM-DD` |

`rows[]` columns:

| Column | Description |
|---|---|
| `response_id`, `received_at`, `timeslot`, `trade_date`, `ticker` | context |
| `snapshot_time`, `spot_price`, `spot_time` | the underlying's instant and price as declared by the payload (more precise than `timeslot`, which comes from the request URL) |
| `expiry` | the tenor's label (`0DTE`, `1D`, … `1M`) |
| `exp_date` | the actual calendar date of that tenor |
| `strike` | the strike price |
| `iv` | implied volatility (can be `null` when the market is closed) |
| `od_value` | the dashboard's raw displayed value |
| `percentile` | position on a 0-100 scale |

---

### `GET /volatility/surface/latest`

The 3D volatility surface (expiration × strike grid).

| Parameter | Type | Values |
|---|---|---|
| `ticker` | string | e.g. `SPX` |
| `trade_date` | string | `YYYY-MM-DD` |

`rows[]` columns:

| Column | Description |
|---|---|
| `response_id`, `received_at`, `timeslot`, `trade_date`, `ticker` | context |
| `snapshot_time`, `spot_price`, `spot_time` | same as above |
| `expiration` | the expiration date |
| `dte` | days to expiration |
| `strike` | the strike price |
| `iv` | implied volatility at this grid point |
| `x_label`, `y_label` | axis labels as declared by the dashboard |
| `range_min`, `range_max` | the expected range (a sanity check, not a hard limit) |

---

### `GET /volatility/slices/latest`

Curves derived from the surface: one expiration's smile, or one
strike's term structure.

| Parameter | Type | Values |
|---|---|---|
| `kind` | string | `smile`, `term-structure` |
| `ticker` | string | e.g. `SPX` |
| `trade_date` | string | `YYYY-MM-DD` |

`rows[]` columns:

| Column | Description |
|---|---|
| `response_id`, `received_at`, `timeslot`, `trade_date`, `ticker` | context |
| `snapshot_time`, `spot_price`, `spot_time` | same as above |
| `kind` | `smile` or `term-structure` |
| `trace_index` | which curve, when several are drawn together — `null` otherwise |
| `anchor_expiration` | for a `smile`: the curve's expiration |
| `anchor_strike` | for a `term-structure`: the curve's strike |
| `expiration`, `strike` | the exact point on the curve |
| `iv` | implied volatility at that point |

---

### `GET /candles/latest`

Intraday OHLC candles.

| Parameter | Type | Values |
|---|---|---|
| `measurement` | string | `es_futures_point`, `es_futures_5_min`, `od_score` |
| `frequency` | string | e.g. `1T` (1 minute) |
| `ticker` | string | e.g. `SPX` |
| `trade_date` | string | `YYYY-MM-DD` |

`rows[]` columns:

| Column | Description |
|---|---|
| `response_id`, `received_at`, `trade_date`, `ticker` | context (no `timeslot` — each row already carries its own `candle_time`) |
| `measurement`, `frequency` | identity of the series |
| `candle_time` | the candle's timestamp, in UTC |
| `open`, `high`, `low`, `close` | the candle's OHLC values |

---

## HTTP status codes

| Code | Meaning |
|---|---|
| `200` | OK, data returned |
| `401` | Missing, invalid, expired, or revoked token |
| `404` | No data matches the given filters |
| `500` | Server misconfigured (signing secret not set) |
| `503` | Database file not found (e.g. cleared between trading sessions, not yet recreated) |

## Further reading

- `docs/endpoints/breakdown-by-strike.md` — the difference between the
  "Strikes" (0DTE) and "StrikeAll" source dashboards
- `docs/endpoints/plotly-gamma.md` — the raw Plotly shape behind the
  heatmap endpoints, before `src/store.py` cleans it up
