"""Are the fingerprints of the records decrypted by Python those of the Rust database?

It is not enough that the fingerprint works in the abstract: here we take the
file Python has just written (`data/decrypted.jsonl`) and check that every
fingerprint already exists in the database of the Rust project. If even one
were missing, the two projects would not recognise each other.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()


def main(argv: list[str]) -> int:
    decrypted = Path(argv[1] if len(argv) > 1 else "data/decrypted.jsonl")
    database = Path(argv[2] if len(argv) > 2 else "../rust_od/data/od.db")

    if not decrypted.exists():
        print(f"❌ there is no {decrypted}: run `python main.py decrypt` first")

        return 1

    if not database.exists():
        print(f"❌ Rust database not found: {database}")

        return 1

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

    known = {row[0] for row in connection.execute("SELECT id FROM responses")}

    connection.close()

    lines = [line for line in decrypted.read_text(encoding="utf-8").splitlines() if line.strip()]

    print(f"records decrypted by Python:    {len(lines)}")
    print(f"responses in the Rust database: {len(known)}")
    print("")

    unknown = 0
    empty = 0

    for line in lines:
        record = json.loads(line)

        if record["fingerprint"] in known:
            continue

        # An **empty** response goes into neither database: the parser
        # rejects it (`"nessuna traccia con le candele"` — the error
        # messages of `src/` are not translated yet) and Rust does the
        # same. It is not a divergence: it is the same decision.
        traces = (record.get("payload") or {}).get("figure", {}).get("data") or []

        if record["endpoint"] == "plotly-candlesticks" and not traces:
            empty += 1

            continue

        # If this one fires, the Rust database is **behind**: the live
        # capture writes into the Python one, and nobody has loaded those
        # responses over there yet. It is not a fingerprint divergence —
        # which is what this test checks — but it has to be said, and it is
        # fixed by loading the same file into Rust.
        unknown += 1

        if unknown <= 5:
            print(f"   ❌ fingerprint unknown to Rust: {record['fingerprint']} ({record['endpoint']})")

            print(
                "      (the Rust database is behind: "
                "`cargo run -- load <this file>` catches it up)"
            )

    print(f"unrecognised fingerprints: {unknown}")

    if empty:
        print(f"empty responses, rejected by both: {empty}")

    print("")

    ok = unknown == 0

    print(
        "✅ every response decrypted by Python is already in the Rust database: "
        "the two versions speak the same language."
        if ok
        else "❌ the two versions diverge."
    )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
