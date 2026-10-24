"""The live chain, without a browser.

A response enters the capture, and it must come out decrypted in the
readable file, written into the database, and **no** longer in the capture.
The responses that do not decrypt must stay where they are.

It is run from `py_od`:

    python tools/check_live.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from actions.decrypt import encrypt_for_test  # noqa: E402
import live as live_module  # noqa: E402

SCRATCH = Path("target_live_check")

URL = (
    "https://backend.optionsdepth.com/api/v1/charts/breakdown-by-strike/"
    "?date=2026-09-30&ticker=SPX&date_time=2026-09-30T13:00:00"
    "&participants=mm_posn&metric=GEX"
)


def strike_record(ticker: str = "SPX") -> str:
    """A minimal but valid `breakdown-by-strike` response."""
    payload = {
        "figure": {
            "data": [
                {"name": "Calls", "x": [1.0], "y": [7600.0], "customdata": [[1.0, 2.0, 3.0]]},
                {"name": "Puts", "x": [2.0], "y": [7600.0]},
                {"name": "Net", "x": [3.0], "y": [7600.0]},
            ]
        }
    }

    data, iv, tag = encrypt_for_test(payload)

    return json.dumps(
        {
            "timestamp": 1790768577,
            "window": "test",
            "method": "GET",
            "url": URL.replace("ticker=SPX", f"ticker={ticker}"),
            "status": 200,
            "body": {"iv": iv, "data": data, "tag": tag},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def post_record() -> str:
    """A response that does not decrypt: it is not a GET."""
    data, iv, tag = encrypt_for_test({"something": True})

    return json.dumps(
        {
            "timestamp": 1790768577,
            "window": "test",
            "method": "POST",
            "url": URL,
            "status": 200,
            "body": {"iv": iv, "data": data, "tag": tag},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def scratch(name: str) -> Path:
    folder = SCRATCH / name

    if folder.exists():
        for item in folder.iterdir():
            item.unlink()

    folder.mkdir(parents=True, exist_ok=True)

    return folder


failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures

    if not ok:
        failures += 1

    print(f"   {'✅' if ok else '❌'} {label}{' · ' + detail if detail else ''}")


print("=== a good response: from the capture to the database ===")

folder = scratch("buona")

capture = folder / "network.jsonl"
decrypted = folder / "decrypted.jsonl"
database = folder / "od.db"

record = strike_record()

capture.write_text(record + "\n", encoding="utf-8")

session = live_module.Live(str(database), capture=str(capture), decrypted=str(decrypted))

session.handle(record)

left = capture.read_text(encoding="utf-8").strip()

check("the row has left the capture", left == "", f"left: {left[:40]!r}")

readable = decrypted.read_text(encoding="utf-8").strip()

check("the readable file has the response", readable.count("\n") == 0 and "breakdown-by-strike" in readable)

import sqlite3  # noqa: E402

connection = sqlite3.connect(database)

responses = connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

rows = connection.execute("SELECT strike, call, put, net FROM breakdown_by_strike").fetchall()

check("the database has the response", responses == 1, f"{responses} responses")

check("and the row says what the payload said", rows == [(7600.0, 1.0, 2.0, 3.0)], f"{rows}")

connection.close()

print("")
print("=== a response that does not decrypt: it stays in the capture ===")

folder = scratch("non_decifrabile")

capture = folder / "network.jsonl"
decrypted = folder / "decrypted.jsonl"
database = folder / "od.db"

record = post_record()

capture.write_text(record + "\n", encoding="utf-8")

session = live_module.Live(str(database), capture=str(capture), decrypted=str(decrypted))

session.handle(record)

left = capture.read_text(encoding="utf-8").strip()

check("the row has stayed in the capture", left == record.strip())

connection = sqlite3.connect(database)

responses = connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

check("the database has nothing", responses == 0)

connection.close()

print("")
print("=== two identical responses ===")

folder = scratch("idempotente")

capture = folder / "network.jsonl"
decrypted = folder / "decrypted.jsonl"
database = folder / "od.db"

record = strike_record()

capture.write_text(record + "\n" + record + "\n", encoding="utf-8")

session = live_module.Live(str(database), capture=str(capture), decrypted=str(decrypted))

session.handle(record)

lines = [line for line in capture.read_text(encoding="utf-8").splitlines() if line.strip()]

check("only one row has left, not all the copies", len(lines) == 1, f"{len(lines)} rows")

connection = sqlite3.connect(database)

responses = connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

check("and the database has a single response", responses == 1, f"{responses} responses")

connection.close()

# The second copy, which stayed, is not written again: it leaves too.
session.handle(lines[0])

remaining = [line for line in capture.read_text(encoding="utf-8").splitlines() if line.strip()]

check("the second copy leaves without writing again", remaining == [], f"{len(remaining)} rows")

connection = sqlite3.connect(database)

responses = connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

check("the database still has a single response", responses == 1)

connection.close()

print("")
print("=== the other rows of the capture are not touched ===")

folder = scratch("altre_righe")

capture = folder / "network.jsonl"
decrypted = folder / "decrypted.jsonl"
database = folder / "od.db"

good = strike_record("SPX")
other = strike_record("NDX")
mute = post_record()

capture.write_text(other + "\n" + mute + "\n" + good + "\n", encoding="utf-8")

session = live_module.Live(str(database), capture=str(capture), decrypted=str(decrypted))

session.handle(good)

left = [line for line in capture.read_text(encoding="utf-8").splitlines() if line.strip()]

check("the other two have stayed", len(left) == 2, f"{len(left)} rows")

check("and they are exactly those", left == [other.strip(), mute.strip()])

print("")

if failures:
    print(f"❌ {failures} checks failed")

    raise SystemExit(1)

print("✅ The live chain does what it must.")
