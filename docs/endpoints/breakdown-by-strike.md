# `breakdown-by-strike`

Source endpoint: `GET /api/v1/charts/breakdown-by-strike/` on `backend.optionsdepth.com`.

One response = one exposure metric, one side (market makers or
customers), for one ticker, at one instant: the exposure (delta, gamma,
vega, …) broken down **per strike**. This is the data behind the
dashboard's "Breakdown by Strike" chart.

## What the request says (the URL's query parameters)

Example, decrypted and captured live:

```
https://backend.optionsdepth.com/api/v1/charts/breakdown-by-strike/
  ?date=2026-10-23
  &ticker=SPX
  &date_time=2026-10-23T12:25:00
  &participants=mm_posn
  &metric=DEX
  &lower_strike_price=200
  &upper_strike_price=20000
  &expiration_range_start=2026-10-23
  &expiration_range_end=2026-10-23
```

| Parameter | Meaning |
|---|---|
| `date` | the trade date the data is about |
| `ticker` | the underlying (`SPX`, …) |
| `date_time` | the instant of the dashboard snapshot (the timeslot) |
| `participants` | which side: `mm_posn` = market makers. Anything else (`firm_posn`, `broker_posn`, `procust_posn`, `nonprocust_posn`) is grouped as **customers** |
| `metric` | the exposure metric: `GEX` (gamma), `VEX` (vega), `DEX` (delta), `CEX` (charm), `NET_POSITION` |
| `lower_strike_price` / `upper_strike_price` | the strike range requested |
| `expiration_range_start` / `expiration_range_end` | the expiration range included in the aggregation — see **Two dashboards, one endpoint** below |

## Two dashboards, one endpoint

Two different widgets in the app's "My Dashboard" picker both call this
same endpoint:

- **"Strikes"** (`config.json` key `Breakdown_By_Strike`) — 0DTE: the
  URL's `expiration_range_start`/`expiration_range_end` are both the
  trade date, so only today's expiration is aggregated
- **"StrikeAll"** (`config.json` key `Breakdown_By_Strike_All`) — every
  expiration: the URL carries **no** `expiration_range_*` at all

Both are stored in the same `breakdown_by_strike` table, told apart only
by the `expiration_scope` column (see below) — they are otherwise
identical in shape, and a row from one is indistinguishable from the
other without it.

## What the response contains (the decrypted payload)

The API sends back a **Plotly figure** — the same object the dashboard's
chart library draws directly — not a plain data table. Everything useful
lives under `payload.figure`.

### `payload.figure.data` — three traces

| Trace `name` | What it is |
|---|---|
| `Calls` | the metric's exposure from call options, per strike |
| `Puts` | the metric's exposure from put options, per strike |
| `Net` | `Calls + Puts` — what the chart actually highlights |

Each trace (the chart is horizontal, `orientation: "h"`):

- **`y`** — the strikes, increasing (one entry per row)
- **`x`** — this trace's value at that strike (so `Calls.x[i]` is the call
  exposure at strike `Calls.y[i]`)
- **`customdata[i]`** — the triple `[call, put, net]` for row `i`,
  **identical across all three traces**. This is what the dashboard's
  tooltip reads to show all three numbers at once when hovering a bar;
  it is redundant with the three `x` arrays, not extra information.
- **`marker.color`**, **`hovertemplate`**, **`showlegend`** — chart
  styling only, not data.

### `payload.figure.layout` — chart presentation

Axis titles, fonts, grid colors, the Plotly default theme. The only
pieces worth reading: `layout.xaxis.title.text` (e.g. `"Net Market Maker
Delta Exposure (∆)"`) and `layout.yaxis.title.text` (`"Strike Price"`) —
they confirm which metric and side the response is about in human words,
matching `metric` and `participants` from the URL.

### The signal-to-noise ratio

A typical response is several thousand lines of JSON; the chart styling
(`marker.color` repeated once per strike, the full Plotly theme object)
accounts for the overwhelming majority of it. The only numbers that
matter are, per strike: `call`, `put`, `net` (one of the three
`customdata` columns, or equivalently one `x` array each).

## What ends up in the database

`src/store.py` (`parse_strike`, table `breakdown_by_strike`) reduces each
response to exactly that — one row per strike, with the identity of the
series attached so a row is self-contained:

| Column | From |
|---|---|
| `response_id` | fingerprint of the encrypted envelope (ties the row back to `responses`) |
| `trade_date` | `date` (URL) |
| `timeslot` | `date_time` (URL), can be `NULL` for a whole-day request |
| `ticker` | `ticker` (URL) |
| `exposure` | `metric` (URL), uppercased — `GEX`, `VEX`, `DEX`, `CEX`, `NET_POSITION` |
| `side` | derived from `participants` — `MM` or `CUSTOMER` |
| `expiration_scope` | derived from `expiration_range_start`/`expiration_range_end` (`store.expiration_scope_of`) — `ALL`, `0DTE`, or a literal `{start}..{end}` for any other range |
| `strike` | `Calls.y[i]` |
| `call` | `customdata[i][0]` |
| `put` | `customdata[i][1]` |
| `net` | `customdata[i][2]` |

Primary key: `(response_id, strike)` — loading the same response twice
writes nothing twice (`INSERT OR IGNORE`).

## Served by the API server

`GET /strikes/latest?exposure=&side=&ticker=&trade_date=&expiration_scope=`
(see `server/main.py`) returns the most recent response matching the
given filters, as the rows above — not the raw Plotly figure. Pass
`expiration_scope=0DTE` or `expiration_scope=ALL` to pick "Strikes" or
"StrikeAll" specifically; without it, the most recent of either.
