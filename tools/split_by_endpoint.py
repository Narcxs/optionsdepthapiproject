"""Writes the **latest** decrypted response of each endpoint as a clean
JSON file, and a Markdown report describing what each one looks like.

    python tools/split_by_endpoint.py [decrypted.jsonl] [output_dir]

Defaults to `data/decrypted.jsonl` → `data/by_endpoint/`. One file per
endpoint (`breakdown-by-strike.json`, …), each holding a single response:
the most recent one (`captured_at`), not the whole history — this is a
snapshot of "what the data looks like right now", not an archive.

The encrypted envelope (`iv`, `data`, `tag`) is dropped: it is there in
`decrypted.jsonl` and in the database for integrity/fingerprinting, not
for reading, and keeping it here only makes the output look like it was
not actually decrypted (the field named `data` is ciphertext, confusingly
enough).

Meant to be re-run after every capture: it always rewrites its output
from the current `decrypted.jsonl`, it does not accumulate across runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from api import endpoints as endpoints_module  # noqa: E402
from api import query  # noqa: E402
from store import expiration_scope_of  # noqa: E402

DEFAULT_SOURCE = "data/decrypted.jsonl"
DEFAULT_OUTPUT = "data/by_endpoint"
REPORT_FILE = "data/ENDPOINTS.md"

# What stays in the cleaned snapshot: the encrypted envelope (`iv`, `data`,
# `tag`) is left out on purpose — see the module docstring.
_KEPT_FIELDS = ("endpoint", "url", "captured_at", "received_at", "fingerprint", "payload")

# Endpoints where two dashboard widgets hit the same URL path and differ
# only by `expiration_range_start`/`expiration_range_end` ("Strikes" vs
# "StrikeAll" — see `store.expiration_scope_of`). Picking one "latest"
# response per endpoint would silently hide whichever one was not the
# most recently captured, so these are split into one file per scope
# instead of one file per endpoint.
_SPLIT_BY_SCOPE = ("breakdown-by-strike",)


def _slug(scope: str) -> str:
    """`expiration_scope` as a filename-safe fragment: "0DTE" -> "0dte",
    "2026-10-05..2026-11-14" -> "2026-10-05_to_2026-11-14"."""
    return scope.lower().replace("..", "_to_").replace(":", "")


def _scope_of(record: dict[str, Any]) -> str | None:
    """The split key for this record, or `None` for endpoints that are
    not split (a single "latest" file is enough for them)."""
    endpoint = record.get("endpoint")

    if endpoint not in _SPLIT_BY_SCOPE:
        return None

    url = record.get("url", "")
    trade_date = query.param(url, "date")

    return expiration_scope_of(url, trade_date)


def load_records(source: Path) -> list[dict[str, Any]]:
    records = []

    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()

            if not line:
                continue

            records.append(json.loads(line))

    return records


def latest_per_endpoint(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str | None], dict[str, Any]]:
    """The most recent response of each (endpoint, scope), by `captured_at`.

    `scope` is `None` for every endpoint not in `_SPLIT_BY_SCOPE` — so
    most endpoints still get exactly one file, same as before.
    """
    latest: dict[tuple[str, str | None], dict[str, Any]] = {}

    for record in records:
        endpoint = record.get("endpoint", "unknown")
        key = (endpoint, _scope_of(record))

        current = latest.get(key)

        if current is None or record.get("captured_at", 0) >= current.get("captured_at", 0):
            latest[key] = record

    return latest


def _cleaned(record: dict[str, Any]) -> dict[str, Any]:
    """The record without the encrypted envelope — only what is readable."""
    return {field: record[field] for field in _KEPT_FIELDS if field in record}


def _file_stem(key: tuple[str, str | None]) -> str:
    endpoint, scope = key

    return endpoint if scope is None else f"{endpoint}--{_slug(scope)}"


def write_snapshot_files(
    snapshots: dict[tuple[str, str | None], dict[str, Any]], output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Cleared first: an endpoint split differently than the last run (or
    # no longer captured at all) must not leave a stale file behind —
    # the module promises "no accumulation across runs".
    for stale in output_dir.glob("*.json"):
        stale.unlink()

    for key, record in snapshots.items():
        path = output_dir / f"{_file_stem(key)}.json"

        path.write_text(
            json.dumps(_cleaned(record), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _display_name(endpoint_token: str) -> str:
    """The human label for an endpoint token, from `api/endpoints.py` when
    known (so the report uses the same names as the console), the raw
    token otherwise."""
    for endpoint in endpoints_module.Endpoint:
        if endpoint.token() == endpoint_token:
            return endpoint.display()

    return endpoint_token


def _payload_preview(payload: Any, max_chars: int = 1200) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + f"\n... ({len(text) - max_chars} more characters, see the .json file)"


def write_report(
    snapshots: dict[tuple[str, str | None], dict[str, Any]],
    output_dir: Path,
    report_path: Path,
) -> None:
    lines: list[str] = []

    lines.append("# Captured endpoints")
    lines.append("")
    lines.append(
        f"The latest snapshot of each of the {len(snapshots)} endpoints captured so far "
        "(some split by `expiration_scope` — two dashboard widgets hitting the same "
        "endpoint, see `docs/endpoints/breakdown-by-strike.md`) — one response per "
        "entry, not the whole history. Generated by `tools/split_by_endpoint.py` from "
        "`data/decrypted.jsonl`; rerun it after every capture, this file is not "
        "hand-maintained."
    )
    lines.append("")

    try:
        output_display = output_dir.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        output_display = output_dir.as_posix()

    lines.append("## Summary")
    lines.append("")
    lines.append("| Endpoint | Scope | Captured at | File |")
    lines.append("|---|---|---|---|")

    for key in sorted(snapshots):
        endpoint, scope = key
        record = snapshots[key]
        relative = f"{output_display}/{_file_stem(key)}.json"

        lines.append(
            f"| `{endpoint}` ({_display_name(endpoint)}) | {scope or '—'} | "
            f"{record.get('received_at', '')} | `{relative}` |"
        )

    lines.append("")

    for key in sorted(snapshots):
        endpoint, scope = key
        record = snapshots[key]

        title = f"## `{endpoint}` — {_display_name(endpoint)}"

        if scope is not None:
            title += f" ({scope})"

        lines.append(title)
        lines.append("")
        lines.append(f"- URL: `{record.get('url', '')}`")
        lines.append(f"- Captured at: `{record.get('received_at', '')}`")
        lines.append("")
        lines.append("<details><summary>Decrypted payload (truncated)</summary>")
        lines.append("")
        lines.append("```json")
        lines.append(_payload_preview(record.get("payload")))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else PROJECT_ROOT / DEFAULT_SOURCE
    output_dir = Path(argv[2]) if len(argv) > 2 else PROJECT_ROOT / DEFAULT_OUTPUT

    if not source.exists():
        print(f"❌ {source} does not exist: nothing to split.")

        return 1

    records = load_records(source)

    if not records:
        print(f"❌ {source} is empty.")

        return 1

    snapshots = latest_per_endpoint(records)

    write_snapshot_files(snapshots, output_dir)

    report_path = PROJECT_ROOT / REPORT_FILE

    write_report(snapshots, output_dir, report_path)

    print(f"✅ {len(snapshots)} endpoints, latest snapshot each, written under {output_dir}")
    print(f"📄 Report: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
