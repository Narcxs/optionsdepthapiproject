"""Single source of truth for the OptionsDepth API endpoints.

Both the capture and the import use this module: the list of captured
endpoints and the list of interpreted endpoints cannot diverge.

Translation of `src/api/endpoints.rs`.
"""

from __future__ import annotations

from enum import Enum


class HeatmapMetric(Enum):
    """The three heatmap metrics: the same data, three endpoints."""

    GAMMA = "GAMMA"
    CHARM = "CHARM"
    VANNA = "VANNA"

    def token(self) -> str:
        return self.value

    def trace_prefix(self) -> str:
        """Name used by Plotly in `figure.data[].name` (e.g. "Gamma Peak")."""
        return self.value.capitalize()


class Endpoint(Enum):
    """All the endpoints the project knows about."""

    STRIKE_BREAKDOWN = "StrikeBreakdown"
    EXPIRATION_BREAKDOWN = "ExpirationBreakdown"
    HEATMAP_GAMMA = "HeatmapGamma"
    HEATMAP_CHARM = "HeatmapCharm"
    HEATMAP_VANNA = "HeatmapVanna"
    CANDLESTICKS = "Candlesticks"
    DEPTH_VIEW = "DepthView"

    VOLATILITY_TABLE = "VolatilityTable"
    VOLATILITY_SURFACE = "VolatilitySurface"
    VOLATILITY_SURFACE_TERM = "VolatilitySurfaceTerm"
    VOLATILITY_SURFACE_COMPANION = "VolatilitySurfaceCompanion"
    VOLATILITY_EXPIRATION_DATES = "VolatilityExpirationDates"
    VOLATILITY_STRIKES = "VolatilityStrikes"
    VOLATILITY_QUOTE_DATES = "VolatilityQuoteDates"
    VOLATILITY_TIME_SLOTS = "VolatilityTimeSlots"
    VOLATILITY_FILTERS = "VolatilityFilters"
    VOLATILITY_FILTERS_RANGE = "VolatilityFiltersRange"
    VOLATILITY_SETTINGS = "VolatilitySettings"

    PRICE_RANGE_LIMITS = "PriceRangeLimits"
    TRADING_DAYS = "TradingDays"
    INTRADAY_TIME_SLOTS = "IntradayTimeSlots"

    CHARTS_INFO = "ChartsInfo"
    CHARTS_VALIDATIONS = "ChartsValidations"

    UNKNOWN = "Unknown"

    # ------------------------------------------------------------------
    # Stable name, used in the database and in the reports
    # ------------------------------------------------------------------

    def token(self) -> str:
        return _TOKENS[self]

    def api_path(self) -> str:
        return _PATHS[self]

    def metric(self) -> str | None:
        """Metric derived from the endpoint (heatmap only)."""
        return {
            Endpoint.HEATMAP_GAMMA: "GAMMA",
            Endpoint.HEATMAP_CHARM: "CHARM",
            Endpoint.HEATMAP_VANNA: "VANNA",
        }.get(self)

    def trace_prefix(self) -> str | None:
        if self in _HEATMAP_KINDS:
            return _HEATMAP_KINDS[self].trace_prefix()

        return None

    def heatmap_metric(self) -> HeatmapMetric | None:
        return _HEATMAP_KINDS.get(self)

    def has_data_table(self) -> bool:
        """`True` if the endpoint produces rows in a data table.

        The others are service (configurations, filters, lists).
        """
        return self in _WITH_DATA_TABLE

    def is_needed(self) -> bool:
        """`False` for the responses that are of no use to the analysis."""
        return self not in _NOT_NEEDED

    def category(self) -> str:
        if self in (Endpoint.STRIKE_BREAKDOWN,):
            return "Strike"

        if self in (Endpoint.EXPIRATION_BREAKDOWN,):
            return "Expiration"

        if self in _HEATMAP_KINDS:
            return "Heatmap"

        if self is Endpoint.CANDLESTICKS:
            return "Candles"

        if self is Endpoint.DEPTH_VIEW:
            return "Depth"

        if self is Endpoint.VOLATILITY_TABLE:
            return "Volatility"

        return "Metadata"

    def display(self) -> str:
        """Readable label for the console."""
        return _DISPLAY[self]


_HEATMAP_KINDS: dict[Endpoint, HeatmapMetric] = {
    Endpoint.HEATMAP_GAMMA: HeatmapMetric.GAMMA,
    Endpoint.HEATMAP_CHARM: HeatmapMetric.CHARM,
    Endpoint.HEATMAP_VANNA: HeatmapMetric.VANNA,
}

_TOKENS: dict[Endpoint, str] = {
    Endpoint.STRIKE_BREAKDOWN: "breakdown-by-strike",
    Endpoint.EXPIRATION_BREAKDOWN: "breakdown-by-expiration",
    Endpoint.HEATMAP_GAMMA: "plotly-gamma",
    Endpoint.HEATMAP_CHARM: "plotly-charm",
    Endpoint.HEATMAP_VANNA: "plotly-vanna",
    Endpoint.CANDLESTICKS: "plotly-candlesticks",
    Endpoint.DEPTH_VIEW: "depth-view-table",
    Endpoint.VOLATILITY_TABLE: "volatility-table",
    Endpoint.VOLATILITY_SURFACE: "volatility-3d-surface",
    Endpoint.VOLATILITY_SURFACE_TERM: "volatility-3d-surface-term-structure",
    Endpoint.VOLATILITY_SURFACE_COMPANION: "volatility-3d-surface-companion",
    Endpoint.VOLATILITY_EXPIRATION_DATES: "volatility-table-expiration-dates",
    Endpoint.VOLATILITY_STRIKES: "volatility-available-strikes",
    Endpoint.VOLATILITY_QUOTE_DATES: "volatility-quote-dates",
    Endpoint.VOLATILITY_TIME_SLOTS: "volatility-time-slot",
    Endpoint.VOLATILITY_FILTERS: "volatility-filters",
    Endpoint.VOLATILITY_FILTERS_RANGE: "volatility-filters-range",
    Endpoint.VOLATILITY_SETTINGS: "volatility-settings",
    Endpoint.PRICE_RANGE_LIMITS: "price-range-limits",
    Endpoint.TRADING_DAYS: "trading-days",
    Endpoint.INTRADAY_TIME_SLOTS: "intraday-timeslots",
    Endpoint.CHARTS_INFO: "charts-info",
    Endpoint.CHARTS_VALIDATIONS: "charts-validations",
    Endpoint.UNKNOWN: "unknown",
}

_PATHS: dict[Endpoint, str] = {
    Endpoint.STRIKE_BREAKDOWN: "/api/v1/charts/breakdown-by-strike",
    Endpoint.EXPIRATION_BREAKDOWN: "/api/v1/charts/breakdown-by-expiration",
    Endpoint.HEATMAP_GAMMA: "/api/v1/charts/plotly-gamma",
    Endpoint.HEATMAP_CHARM: "/api/v1/charts/plotly-charm",
    Endpoint.HEATMAP_VANNA: "/api/v1/charts/plotly-vanna",
    Endpoint.CANDLESTICKS: "/api/v1/charts/plotly-candlesticks",
    Endpoint.DEPTH_VIEW: "/api/v1/charts/depth-view-table",
    Endpoint.VOLATILITY_TABLE: "/api/v1/volatility/table",
    Endpoint.VOLATILITY_SURFACE: "/api/v1/volatility/3d-surface",
    Endpoint.VOLATILITY_SURFACE_TERM: "/api/v1/volatility/3d-surface/term-structure",
    Endpoint.VOLATILITY_SURFACE_COMPANION: "/api/v1/volatility/3d-surface/companion",
    Endpoint.VOLATILITY_EXPIRATION_DATES: "/api/v1/volatility/table-expiration-dates",
    Endpoint.VOLATILITY_STRIKES: "/api/v1/volatility/available-strikes",
    Endpoint.VOLATILITY_QUOTE_DATES: "/api/v1/volatility/quote-dates",
    Endpoint.VOLATILITY_TIME_SLOTS: "/api/v1/volatility/time-slot",
    Endpoint.VOLATILITY_FILTERS: "/api/v1/volatility/filters",
    Endpoint.VOLATILITY_FILTERS_RANGE: "/api/v1/volatility/filters-range",
    Endpoint.VOLATILITY_SETTINGS: "/api/v1/volatility/settings",
    Endpoint.PRICE_RANGE_LIMITS: "/api/v1/charts/mm-exposure-price-range-limits",
    Endpoint.TRADING_DAYS: "/api/v1/charts/trading-days",
    Endpoint.INTRADAY_TIME_SLOTS: "/api/v1/charts/intraday-timeslots",
    Endpoint.CHARTS_INFO: "/api/v1/charts/charts-info",
    Endpoint.CHARTS_VALIDATIONS: "/api/v1/charts/charts-validations",
    Endpoint.UNKNOWN: "",
}

_DISPLAY: dict[Endpoint, str] = {
    Endpoint.STRIKE_BREAKDOWN: "Breakdown by Strike",
    Endpoint.EXPIRATION_BREAKDOWN: "Breakdown by Expiration",
    Endpoint.HEATMAP_GAMMA: "Heatmap Gamma",
    Endpoint.HEATMAP_CHARM: "Heatmap Charm",
    Endpoint.HEATMAP_VANNA: "Heatmap Vanna",
    Endpoint.CANDLESTICKS: "Candles",
    Endpoint.DEPTH_VIEW: "Depth View",
    Endpoint.VOLATILITY_TABLE: "Volatility Table",
    Endpoint.VOLATILITY_SURFACE: "Volatility Surface",
    Endpoint.VOLATILITY_SURFACE_TERM: "Volatility Surface · Term Structure",
    Endpoint.VOLATILITY_SURFACE_COMPANION: "Volatility Surface · Companion",
    Endpoint.VOLATILITY_EXPIRATION_DATES: "Volatility Expiration Dates",
    Endpoint.VOLATILITY_STRIKES: "Volatility Strikes",
    Endpoint.VOLATILITY_QUOTE_DATES: "Volatility Quote Dates",
    Endpoint.VOLATILITY_TIME_SLOTS: "Volatility Time Slots",
    Endpoint.VOLATILITY_FILTERS: "Volatility Filters",
    Endpoint.VOLATILITY_FILTERS_RANGE: "Volatility Filters Range",
    Endpoint.VOLATILITY_SETTINGS: "Volatility Settings",
    Endpoint.PRICE_RANGE_LIMITS: "Price Range Limits",
    Endpoint.TRADING_DAYS: "Trading Days",
    Endpoint.INTRADAY_TIME_SLOTS: "Intraday Time Slots",
    Endpoint.CHARTS_INFO: "Charts Info",
    Endpoint.CHARTS_VALIDATIONS: "Charts Validations",
    Endpoint.UNKNOWN: "Unknown",
}

_WITH_DATA_TABLE = frozenset(
    {
        Endpoint.STRIKE_BREAKDOWN,
        Endpoint.EXPIRATION_BREAKDOWN,
        Endpoint.HEATMAP_GAMMA,
        Endpoint.HEATMAP_CHARM,
        Endpoint.HEATMAP_VANNA,
        Endpoint.CANDLESTICKS,
        Endpoint.DEPTH_VIEW,
        Endpoint.VOLATILITY_TABLE,
        Endpoint.VOLATILITY_SURFACE,
        Endpoint.VOLATILITY_SURFACE_TERM,
        Endpoint.VOLATILITY_SURFACE_COMPANION,
    }
)

_NOT_NEEDED = frozenset(
    {
        Endpoint.CHARTS_INFO,
        Endpoint.TRADING_DAYS,
        Endpoint.INTRADAY_TIME_SLOTS,
    }
)


# ======================================================
# DETECTION
# ======================================================


def url_path(url: str) -> str:
    """Extracts the path from the URL, without query string and without host."""
    without_query = url.split("?", 1)[0]

    if "://" in without_query:
        after_scheme = without_query.split("://", 1)[1]

        slash = after_scheme.find("/")

        return "/" if slash < 0 else after_scheme[slash:]

    return without_query


def normalized_path(url: str) -> str:
    """Path without the trailing slash (except the root)."""
    path = url_path(url)

    if len(path) > 1:
        return path.rstrip("/")

    return path


_DETECT: dict[str, Endpoint] = {
    "/api/v1/charts/breakdown-by-strike": Endpoint.STRIKE_BREAKDOWN,
    "/api/v1/charts/breakdown-by-expiration": Endpoint.EXPIRATION_BREAKDOWN,
    "/api/v1/charts/plotly-gamma": Endpoint.HEATMAP_GAMMA,
    "/api/v1/charts/plotly-charm": Endpoint.HEATMAP_CHARM,
    "/api/v1/charts/plotly-vanna": Endpoint.HEATMAP_VANNA,
    "/api/v1/charts/plotly-candlesticks": Endpoint.CANDLESTICKS,
    "/api/v1/charts/depth-view-table": Endpoint.DEPTH_VIEW,
    "/api/v1/volatility/table": Endpoint.VOLATILITY_TABLE,
    "/api/v1/volatility/3d-surface": Endpoint.VOLATILITY_SURFACE,
    "/api/v1/volatility/3d-surface/term-structure": Endpoint.VOLATILITY_SURFACE_TERM,
    "/api/v1/volatility/3d-surface/companion": Endpoint.VOLATILITY_SURFACE_COMPANION,
    "/api/v1/volatility/table-expiration-dates": Endpoint.VOLATILITY_EXPIRATION_DATES,
    "/api/v1/volatility/available-strikes": Endpoint.VOLATILITY_STRIKES,
    "/api/v1/volatility/quote-dates": Endpoint.VOLATILITY_QUOTE_DATES,
    "/api/v1/volatility/time-slot": Endpoint.VOLATILITY_TIME_SLOTS,
    "/api/v1/volatility/filters": Endpoint.VOLATILITY_FILTERS,
    "/api/v1/volatility/filters-range": Endpoint.VOLATILITY_FILTERS_RANGE,
    "/api/v1/volatility/settings": Endpoint.VOLATILITY_SETTINGS,
    "/api/v1/charts/mm-exposure-price-range-limits": Endpoint.PRICE_RANGE_LIMITS,
    "/api/v1/charts/trading-days": Endpoint.TRADING_DAYS,
    "/api/v1/charts/intraday-timeslots": Endpoint.INTRADAY_TIME_SLOTS,
    "/api/v1/charts/charts-info": Endpoint.CHARTS_INFO,
    "/api/v1/charts/charts-validations": Endpoint.CHARTS_VALIDATIONS,
}


def detect(url: str) -> Endpoint:
    """Which endpoint is this URL (`UNKNOWN` if we do not know it)."""
    return _DETECT.get(normalized_path(url), Endpoint.UNKNOWN)


def token_of(endpoint: Endpoint) -> str:
    """The token of an endpoint, for those who work with tokens."""
    return endpoint.token()


# ======================================================
# CAPTURE
# ======================================================

# API areas that the browser logger records: deliberately broad prefixes.
TRACKED_PREFIXES = ("/api/v1/charts/", "/api/v1/volatility/")


def is_tracked(url: str) -> bool:
    """Is this URL in an area we follow, and is it a response we need?"""
    path = normalized_path(url)

    in_area = any(path.startswith(prefix.rstrip("/")) for prefix in TRACKED_PREFIXES)

    return in_area and detect(url).is_needed()
