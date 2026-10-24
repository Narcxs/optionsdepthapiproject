"""Dimensions of an API response.

They are the coordinates written on the rows of data: metric, market side,
participants, ticker, instant, unit of measurement. Every data table
carries only the dimensions it needs (candlesticks, for example, have
neither metric nor side).

Translation of `src/dimensions.rs`.
"""

from __future__ import annotations

from dataclasses import dataclass

from api import query
from api.endpoints import Endpoint

# Participant types that make up the customer side.
CUSTOMER_PARTS = (
    "firm_posn",
    "broker_posn",
    "procust_posn",
    "nonprocust_posn",
)


@dataclass
class Dimensions:
    """The coordinates of a series."""

    metric: str | None = None
    participants: str | None = None
    side: str | None = None
    ticker: str | None = None
    quote_date: str | None = None
    date_time: str | None = None
    measurement: str | None = None
    frequency: str | None = None

    @classmethod
    def from_request(cls, endpoint: Endpoint, url: str) -> Dimensions:
        participants = query.param(url, "participants")

        return cls(
            metric=canonical_metric(query.param(url, "metric"), endpoint),
            participants=participants,
            side=side_of(participants),
            ticker=query.first_param(url, ["ticker", "symbol", "underlying"]),
            quote_date=query.first_param(url, ["quote_date", "date"]),
            date_time=query.first_param(url, ["date_time", "quote_datetime"]),
            measurement=query.param(url, "measurement"),
            frequency=query.param(url, "frequency"),
        )

    def label(self, endpoint: Endpoint) -> str:
        """Readable label: view, metric, side.

        Examples: `Breakdown by Strike · GEX · MM`,
        `Candles · es_futures_point`.
        """
        parts = [endpoint.display()]

        if self.metric:
            parts.append(self.metric)

        if self.side:
            parts.append(self.side)

        # Candlesticks have no metric: the unit of measurement is what
        # identifies them.
        if not self.metric and self.measurement:
            parts.append(self.measurement)

        return " · ".join(parts)

    def time_label(self, captured_at: int) -> str:
        """When the response is about, in short form, and honest about it.

        The instant of the dashboard (`date_time` in the URL) is what the
        response **says it is about**, and it is preferred: two responses
        from different moments then read as different moments.

        When the URL does not carry it, the only thing known is **when the
        response arrived** — and that is not the same thing. It is marked
        with `≈`, because seven series of the same dashboard arrive within
        the same second and would otherwise print the same minute seven
        times, which reads like the program standing still. It is not: they
        are seven different series of the same instant.
        """
        from actions import decode

        if self.date_time:
            label = decode.italian_time(self.date_time)

            if label:
                return label

        return "≈" + decode.unix_time(captured_at)


def canonical_metric(raw: str | None, endpoint: Endpoint) -> str | None:
    """The dashboard writes metrics in uppercase (`GEX`) except the
    heatmap greeks (`gamma`), which however are not a parameter but derive
    from the endpoint. Here it becomes a single canonical form.
    """
    if raw is not None:
        trimmed = raw.strip()

        if trimmed:
            return trimmed.upper()

    metric = endpoint.metric()

    return metric


def side_of(participants: str | None) -> str | None:
    """From the `participants` parameter to the market side."""
    if participants is None:
        return None

    parts = [part.strip() for part in participants.lower().split(",")]

    parts = [part for part in parts if part]

    market_maker = "mm_posn" in parts

    customer = any(part in CUSTOMER_PARTS for part in parts)

    if market_maker and not customer:
        return "MM"

    if customer and not market_maker:
        return "CUSTOMER"

    if market_maker and customer:
        return "MIXED"

    return "OTHER"
