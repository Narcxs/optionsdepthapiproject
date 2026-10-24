# `plotly-gamma` (and its siblings `plotly-charm`, `plotly-vanna`)

Source endpoint: `GET /api/v1/charts/plotly-gamma/` on
`backend.optionsdepth.com`. `plotly-charm` and `plotly-vanna` are the
same shape of response, the metric is only in the endpoint name — not a
parameter.

This is the data behind the dashboard's **gamma/charm/vanna heatmap**:
one value per (instant × underlying price), for one trading day. This
is the biggest response of the whole project by cell count (~100k cells
for a full day, verified against real captures).

## What the request says (the URL's query parameters)

Example, decrypted and captured live:

```
https://backend.optionsdepth.com/api/v1/charts/plotly-gamma/
  ?ticker=SPX
  &date=2026-10-23
  &is_upcoming_day=false
  &date_time=2026-10-23T12:25:00
  &is_z_score=false
  &measurement=es_futures_point
  &color_scale_min=-1000
  &color_scale_max=1000
  &default_price_range={"anchor":"previous_close","range":1.5,"unit":"%"}
  &implied_range=[{"enabled":true,"sigma":0.5,...}, {"sigma":1,...}, {"sigma":2,...}]
```

| Parameter | Meaning |
|---|---|
| `ticker`, `date` | the underlying and the trading day |
| `date_time` | the instant the dashboard snapshot was taken at |
| `measurement` | the unit of the heatmap's values (`es_futures_point`, `od_score`, …) |
| `color_scale_min` / `color_scale_max` | the color scale's bounds — **presentation only**, the values are not clamped to it |
| `implied_range` | which σ bands to draw around spot (0.5σ, 1σ, 2σ here) — drives the overlay lines, not the grid |
| `default_price_range`, `is_upcoming_day`, `is_z_score` | dashboard display options, not filters on the data |

## What the response contains (the decrypted payload)

Three top-level keys: `figure`, `cross_hair`, `candlesticks`.

- `cross_hair` — the mouse-crosshair's colors/visibility. UI only.
- `candlesticks` — `{bullish: {fill, border_wick}, bearish: {...}}`, two
  hex colors. **Not actual candle data** (that lives in the separate
  `plotly-candlesticks` endpoint) — just the color scheme the chart uses
  if it overlays candles on top of the heatmap.
- `figure.data` — the traces. A real capture had **10** of them:

| # | Trace | Shape | What it is |
|---|---|---|---|
| 0 | `Gamma<br>(ES futures / point)` | `x`, `y`, `z` | **the grid — the only one that matters** |
| 1–6 | `±0.5σ · Rolling`, `±1σ · Rolling`, `±2σ · Rolling` (×2 each) | `x`, `y` lines | the implied-range bands requested via `implied_range` — a line per band edge |
| 7–9 | `Gamma Peak`, `Gamma Trough`, `Gamma Zero` | `lines: [{x, y}, ...]`, several disjoint segments | reference curves the dashboard draws over the heatmap — **not** a single `x`/`y` pair, but a list of line segments (gamma flips sign more than once during the day) |

### The grid (trace 0) — the actual heatmap

- **`x`** — the instants, one per 5-minute slot (`"2026-10-23T09:00:00"`,
  `...T09:05:00"`, … — 85 entries for a regular session)
- **`y`** — the underlying prices on the grid (92 entries, increasing,
  e.g. `7551.5, 7554.0, 7556.5, ...`)
- **`z[row][col]`** — the value at `y[row]` × `x[col]`: `z` has as many
  rows as `y`, each row as long as `x`
- **`zmin` / `zmax`** — repeat the URL's `color_scale_min`/`max`

### Why the other 9 traces are not noise, but are not stored either

Unlike `breakdown-by-strike`'s pure styling clutter, these overlay traces
(σ bands, peak/trough/zero) are real derived series the dashboard
computes and draws — just not ones this project's database captures.
`src/store.py`'s `_heatmap_grid` deliberately picks **only** the trace
that has `x`, `y` **and** `z` together, and raises an error if it finds
more than one such trace (so a future API change that adds a second grid
trace fails loudly instead of silently picking the wrong one). The σ
bands and peak/trough/zero lines are read by the chart library directly
from this same payload, not from the database.

## What ends up in the database

`parse_heatmap` + table `heatmaps` turn the grid into one row per cell:

| Column | From |
|---|---|
| `response_id` | fingerprint of the encrypted envelope |
| `trade_date` | `date` (URL) |
| `timeslot` | `date_time` (URL) |
| `ticker` | `ticker` (URL) |
| `metric` | derived from the endpoint — `GAMMA`, `CHARM` or `VANNA` (uppercase) |
| `measurement` | `measurement` (URL) |
| `time_slot` | `x[col]`, reformatted (`"T"` → space) |
| `price` | `y[row]` |
| `value` | `z[row][col]` |
| `color_scale_min` / `color_scale_max` | from the URL |

Primary key: `(response_id, time_slot, price)`. The loader rejects the
whole response if any row of `z` is shorter than `x`, or if the same
`(time_slot, price)` cell appears twice — a malformed grid is not loaded
partially.

## Served by the API server

`GET /heatmaps/latest?metric=GAMMA&measurement=&ticker=&trade_date=`
(see `server/main.py`) returns the most recent matching response as the
grid cells above — not the raw Plotly figure, and without the σ-band or
peak/trough/zero overlays (they are not persisted).
