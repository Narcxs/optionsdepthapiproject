"""Inspection of the captured payloads: the shape, and the values.

Two tools:

  `inspect`  reads the captures, decrypts, and writes into
             `data/schema_report.json` how the payload of every endpoint is
             made: types, keys, how many traces, what lengths.
  `probe`    shows the real **values** of an endpoint, decrypted: it is
             there to check that the parser reads the right fields.

Translation of `src/actions/inspect.rs` and `src/actions/probe.rs`.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from actions import decode as decode_action
from actions.decode import DecodeError
from actions.decrypt import DecryptError, decrypt_response
from api import endpoints, query

SCHEMA_REPORT = "data/schema_report.json"


# ======================================================
# THE SHAPE
# ======================================================


def type_name(value: Any) -> str:
    """The type of a value, as it is read in a report."""
    if value is None:
        return "null"

    if isinstance(value, bool):
        return "bool"

    if isinstance(value, int):
        return "int"

    if isinstance(value, float):
        return "float"

    if isinstance(value, str):
        return "string"

    if isinstance(value, list):
        return "array"

    if isinstance(value, dict):
        return "object"

    return type(value).__name__


def structure(value: Any, depth: int = 0, max_depth: int = 3) -> Any:
    """The shape of a value: types, keys, lengths.

    It stops at `max_depth`: a 33 MB payload is not described all the way
    down, and there is no need to.
    """
    if depth >= max_depth:
        return type_name(value)

    if isinstance(value, dict):
        return {key: structure(item, depth + 1, max_depth) for key, item in list(value.items())[:40]}

    if isinstance(value, list):
        if not value:
            return []

        # All the equal elements are described once only.
        shapes = {json.dumps(structure(item, depth + 1, max_depth), sort_keys=True) for item in value[:20]}

        if len(shapes) == 1:
            return [structure(value[0], depth + 1, max_depth), f"… ×{len(value)}"]

        return [structure(value[0], depth + 1, max_depth), f"… {len(shapes)} different shapes"]

    return type_name(value)


def trace_report(index: int, trace: Any) -> dict[str, Any]:
    """How a Plotly trace is made."""
    if not isinstance(trace, dict):
        return {"index": index, "type": type_name(trace)}

    def length(key: str) -> int | None:
        values = trace.get(key)

        return len(values) if isinstance(values, list) else None

    def rows_cols(key: str) -> tuple[int | None, int | None]:
        values = trace.get(key)

        if not isinstance(values, list) or not values:
            return None, None

        first = values[0]

        return len(values), len(first) if isinstance(first, list) else None

    z_rows, z_cols = rows_cols("z")
    cd_rows, cd_cols = rows_cols("customdata")

    return {
        "index": index,
        "type": trace.get("type"),
        "name": trace.get("name"),
        "keys": sorted(trace.keys()),
        "x_len": length("x"),
        "y_len": length("y"),
        "z_rows": z_rows,
        "z_cols": z_cols,
        "customdata_rows": cd_rows,
        "customdata_cols": cd_cols,
        "shape": structure(trace, max_depth=2),
    }


def sample_report(record: dict[str, Any], payload: Any) -> dict[str, Any]:
    """A sample: how the payload of this response is made."""
    root: dict[str, Any] = {
        "url": record.get("url"),
        "window": record.get("window"),
        "root_type": type_name(payload),
        "root_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
        "structure": structure(payload),
    }

    if isinstance(payload, dict):
        figure = payload.get("figure")

        if isinstance(figure, dict) and isinstance(figure.get("data"), list):
            traces = figure["data"]

            root["figure_data_count"] = len(traces)
            root["traces"] = [trace_report(index, trace) for index, trace in enumerate(traces)]

    return root


def inspect_files(files: list[str], output: str = SCHEMA_REPORT, samples: int = 2) -> dict[str, Any]:
    """Reads the captures and writes the report of the payload shapes."""
    report: dict[str, Any] = {}

    total = 0
    decrypted = 0
    errors = 0

    for path in files:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError as error:
            print(f"❌ {path}: {error}")

            continue

        for line in lines:
            for chunk in decode_action.split_records(line.strip()):
                if not chunk:
                    continue

                try:
                    record = json.loads(chunk)
                except json.JSONDecodeError:
                    continue

                total += 1

                url = record.get("url") or ""

                endpoint = endpoints.detect(url)

                name = endpoint.token()

                entry = report.setdefault(
                    name,
                    {
                        "endpoint": name,
                        "display": endpoint.display(),
                        "requests": 0,
                        "windows": Counter(),
                        "methods": Counter(),
                        "statuses": Counter(),
                        "metrics": Counter(),
                        "tickers": Counter(),
                        "root_types": Counter(),
                        "root_keys": set(),
                        "samples": [],
                    },
                )

                entry["requests"] += 1
                entry["windows"][record.get("window") or "-"] += 1
                entry["methods"][record.get("method") or "-"] += 1
                entry["statuses"][record.get("status") or 0] += 1

                metric = query.param(url, "metric")

                if metric:
                    entry["metrics"][metric] += 1

                ticker = query.first_param(url, ["ticker", "symbol", "underlying"])

                if ticker:
                    entry["tickers"][ticker] += 1

                body = record.get("body")

                if not isinstance(body, dict):
                    continue

                iv = body.get("iv")
                data = body.get("data")
                tag = body.get("tag")

                if not (iv and data and tag):
                    continue

                try:
                    payload = decrypt_response(data, iv, tag)
                except (DecryptError, DecodeError):
                    errors += 1

                    continue

                decrypted += 1

                entry["root_types"][type_name(payload)] += 1

                if isinstance(payload, dict):
                    entry["root_keys"] |= set(payload.keys())

                if len(entry["samples"]) < samples:
                    entry["samples"].append(sample_report(record, payload))

    # The counters are not JSON: they become ordered objects.
    for entry in report.values():
        for key in ("windows", "methods", "statuses", "metrics", "tickers", "root_types"):
            entry[key] = dict(entry[key].most_common())

        entry["root_keys"] = sorted(entry["root_keys"])

    result = {
        "capture_files": files,
        "records": total,
        "decrypted": decrypted,
        "errors": errors,
        "endpoints": report,
    }

    out = Path(output)

    out.parent.mkdir(parents=True, exist_ok=True)

    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


# ======================================================
# THE VALUES
# ======================================================


def probe_files(files: list[str], wanted: str, limit: int = 1) -> int:
    """Shows the real values of an endpoint (`all` for all of them)."""
    shown = 0

    for path in files:
        if shown >= limit:
            break

        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError as error:
            print(f"❌ {path}: {error}")

            continue

        for line in lines:
            if shown >= limit:
                break

            for chunk in decode_action.split_records(line.strip()):
                if not chunk or shown >= limit:
                    continue

                try:
                    record = json.loads(chunk)
                except json.JSONDecodeError:
                    continue

                endpoint = endpoints.detect(record.get("url") or "")

                if wanted != "all" and wanted not in endpoint.token() and wanted not in endpoint.display().lower():
                    continue

                try:
                    decoded = decode_action.decode(record)
                except DecodeError as error:
                    print(f"⚠️ {error}")

                    continue

                shown += 1

                print("========================================")
                print(f"📥 {decoded.label()}")
                print(f"   {decoded.url}")
                print("========================================")
                print("")

                print(json.dumps(decoded.payload, ensure_ascii=False, indent=2)[:6000])
                print("")

    if shown == 0:
        print(f"❌ No sample for '{wanted}' (try: strike, depth, volatile, all)")

        return 1

    return 0
