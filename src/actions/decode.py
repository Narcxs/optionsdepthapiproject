"""Decoding: from a captured response to the readable payload.

It is all the project needs (capture + decryption):

  · it receives a record of the capture (`data/network.jsonl`)
  · it decrypts the AES-256-GCM envelope (`data`, `iv`, `tag`)
  · it says what that response is, in one console line
  · it writes the readable record into `data/decrypted.jsonl`

It does not interpret the values: the payload comes out **identical** to
the way the API sent it. Translation of `src/actions/decode.rs`.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from actions.decrypt import DecryptError, decrypt_response
from api import endpoints
from api.endpoints import Endpoint
from api import query
from dimensions import Dimensions

# ======================================================
# DECRYPTED LINE
# ======================================================


@dataclass
class Decoded:
    """A decrypted response, with the encrypted original beside it."""

    endpoint: Endpoint
    dimensions: Dimensions
    payload: Any
    captured_at: int
    url: str

    iv: str
    data: str
    tag: str

    def values(self) -> int:
        """How many values the response holds (for the console line)."""
        return count_values(self.endpoint, self.payload)

    def label(self) -> str:
        """What this response is called.

        An unrecognized endpoint introduces itself with its path: better
        than "unknown".
        """
        if self.endpoint is not Endpoint.UNKNOWN:
            return self.dimensions.label(self.endpoint)

        return f"Unrecognized {endpoints.url_path(self.url)}"

    def line(self) -> str:
        """Console line: what it is, when, how many values."""
        return f"📥 {self.label()} · {self.dimensions.time_label(self.captured_at)} · {self.values()} values"

    def record(self) -> dict[str, Any]:
        """Record to write into `data/decrypted.jsonl`.

        It also carries the encrypted original (`iv`, `data`, `tag`) and
        its fingerprint: that way the database is filled from this file
        alone and keeps the exact body the API sent.
        """
        return {
            "captured_at": self.captured_at,
            "received_at": readable_utc(self.captured_at),
            "endpoint": self.endpoint.token(),
            "url": self.url,
            "fingerprint": fingerprint(self.iv, self.data, self.tag),
            "iv": self.iv,
            "data": self.data,
            "tag": self.tag,
            "payload": self.payload,
        }


class DecodeError(Exception):
    """The record is not a decryptable response."""


# ======================================================
# DECRYPTION OF A RECORD
# ======================================================


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        if value.strip() == "":
            return None

        return value

    return None


def _encrypted_body(record: Any) -> dict[str, Any]:
    """The encrypted body can be an object or a JSON string."""
    body = record.get("body") if isinstance(record, dict) else None

    if isinstance(body, dict):
        return body

    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise DecodeError(f"unreadable body: {error}") from error

        if isinstance(parsed, dict):
            return parsed

    raise DecodeError("missing body")


def split_records(line: str) -> list[str]:
    """Splits a line that holds several JSON records stuck together.

    The concurrent appends of the browser have no separator: two responses
    that landed in the same instant can sit on the same line. It is split
    at the start of the next record, so that none of them is lost.
    """
    if line.count('{"timestamp"') <= 1:
        return [line]

    parts = line.split('{"timestamp"')

    records = []

    for index, part in enumerate(parts):
        if not part.strip():
            continue

        # The first piece is a whole record; the others have lost the
        # prefix that opened them.
        records.append(part if index == 0 else '{"timestamp"' + part)

    return records


def decode(record: Any) -> Decoded:
    """Decrypts the capture record.

    `DecodeError` if it is not a decryptable response (not a GET,
    incomplete envelope, wrong key).
    """
    url = _text(record.get("url")) or ""

    method = _text(record.get("method")) or "GET"

    if method != "GET":
        raise DecodeError(f"method {method}")

    endpoint = endpoints.detect(url)

    body = _encrypted_body(record)

    data = _text(body.get("data"))
    iv = _text(body.get("iv"))
    tag = _text(body.get("tag"))

    if data is None or iv is None or tag is None:
        raise DecodeError("incomplete envelope: 'data', 'iv' and 'tag' are required")

    try:
        payload = decrypt_response(data, iv, tag)
    except DecryptError as error:
        raise DecodeError(str(error)) from error

    captured_at = record.get("timestamp")

    if not isinstance(captured_at, int):
        captured_at = 0

    return Decoded(
        endpoint=endpoint,
        dimensions=Dimensions.from_request(endpoint, url),
        payload=payload,
        captured_at=captured_at,
        url=url,
        iv=iv,
        data=data,
        tag=tag,
    )


# ======================================================
# THE FINGERPRINT
# ======================================================

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x00000100000001B3
_MASK64 = 0xFFFFFFFFFFFFFFFF


def fingerprint(iv: str, data: str, tag: str) -> str:
    """Fingerprint of the encrypted body: it identifies the response.

    64-bit FNV-1a over `iv|data|tag`, in lowercase hexadecimal with 16
    digits. It is **the same** fingerprint as the Rust project: if the two
    diverged, the two databases would not recognize each other.
    """
    hash_value = _FNV_OFFSET

    for byte in f"{iv}|{data}|{tag}".encode("utf-8"):
        hash_value ^= byte
        hash_value = (hash_value * _FNV_PRIME) & _MASK64

    return f"{hash_value:016x}"


# ======================================================
# HOW MANY VALUES
#
# A light count, without interpreting the numbers: it only has to say
# "238 values" instead of "one response".
# ======================================================


def _figure_traces(payload: Any) -> list[Any] | None:
    if not isinstance(payload, dict):
        return None

    figure = payload.get("figure")

    if not isinstance(figure, dict):
        return None

    data = figure.get("data")

    if isinstance(data, list):
        return data

    return None


def _trace_lengths(traces: list[Any], key: str) -> int:
    total = 0

    for trace in traces:
        if not isinstance(trace, dict):
            continue

        values = trace.get(key)

        if isinstance(values, list):
            total += len(values)

    return total


def count_values(endpoint: Endpoint, payload: Any) -> int:
    """How many values there are in the response (for the console line)."""
    # breakdown: figure.data[].x — one row per strike / expiration
    if endpoint in (Endpoint.STRIKE_BREAKDOWN, Endpoint.EXPIRATION_BREAKDOWN):
        traces = _figure_traces(payload)

        return 0 if traces is None else _trace_lengths(traces, "x")

    # heatmap: figure.data[0].z — one cell per instant × price
    if endpoint.heatmap_metric() is not None:
        traces = _figure_traces(payload)

        if not traces:
            return 0

        first = traces[0]

        if not isinstance(first, dict):
            return 0

        z = first.get("z")

        if not isinstance(z, list):
            return 0

        return sum(len(row) for row in z if isinstance(row, list))

    # depth view: header (columns) × data (rows)
    if endpoint is Endpoint.DEPTH_VIEW:
        data = payload.get("data") if isinstance(payload, dict) else None

        if not isinstance(data, list):
            return 0

        return sum(len(row) for row in data if isinstance(row, list))

    # candlesticks: figure.data[0].close — one row per candle
    if endpoint is Endpoint.CANDLESTICKS:
        traces = _figure_traces(payload)

        if not traces:
            return 0

        first = traces[0]

        if not isinstance(first, dict):
            return 0

        close = first.get("close")

        return len(close) if isinstance(close, list) else 0

    # volatility: the `data.data` grid × the header columns
    if endpoint is Endpoint.VOLATILITY_TABLE:
        data = payload.get("data") if isinstance(payload, dict) else None

        if not isinstance(data, dict):
            return 0

        rows = data.get("data")

        if not isinstance(rows, list):
            return 0

        total = 0

        for row in rows:
            if not isinstance(row, dict):
                continue

            # The service keys are not cells.
            total += len([key for key in row if key not in _VOLATILITY_SERVICE_KEYS])

        return total

    # surface and slices: the `z` grid, or the points of the traces
    if endpoint in (
        Endpoint.VOLATILITY_SURFACE,
        Endpoint.VOLATILITY_SURFACE_TERM,
        Endpoint.VOLATILITY_SURFACE_COMPANION,
    ):
        traces = _figure_traces(payload)

        if not traces:
            return 0

        first = traces[0]

        if not isinstance(first, dict):
            return 0

        z = first.get("z")

        if isinstance(z, list):
            return sum(len(row) for row in z if isinstance(row, list))

        return _trace_lengths(traces, "y")

    return 0


_VOLATILITY_SERVICE_KEYS = frozenset(
    {
        "expiry",
        "exp_date",
        "dte",
        "dtes",
        "label",
        "color",
        "text_color",
    }
)


# ======================================================
# WRITING THE DECRYPTED FILE
# ======================================================


def append_decoded(path: str | Path, decoded: Decoded) -> None:
    """Appends the decrypted record to the file, one line per response.

    `ensure_ascii=False` so as not to bloat the file with `\\uXXXX`, and
    tight `separators` the way `serde_json` does (no space after `,` and
    `:`), so the two projects write the same text.
    """
    path = Path(path)

    path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(
        decoded.record(),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


# ======================================================
# TIME
# ======================================================

MONTHS = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")


def readable_utc(captured_at: int) -> str:
    """Unix timestamp → `2026-09-30 11:42:57` (UTC)."""
    try:
        moment = dt.datetime.fromtimestamp(captured_at, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""

    return moment.strftime("%Y-%m-%d %H:%M:%S")


def italian_time(value: str) -> str | None:
    """`2026-09-30T07:41:00` → `30 set 07:41`."""
    if "T" not in value:
        return None

    date, time = value.split("T", 1)

    parts = date.split("-")

    if len(parts) < 3:
        return None

    try:
        month = int(parts[1])
    except ValueError:
        return None

    day = parts[2]

    if month < 1 or month > 12:
        return None

    hour_minute = time[:5]

    return f"{day} {MONTHS[month - 1]} {hour_minute}"


def unix_time(value: int) -> str:
    """Unix timestamp → `30 set 11:42`."""
    try:
        moment = dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return str(value)

    month = MONTHS[moment.month - 1]

    return f"{moment.day} {month} {moment.hour:02d}:{moment.minute:02d}"
