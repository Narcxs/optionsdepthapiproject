"""Test that Python's fingerprint is Rust's.

It reads the **real database of the Rust project**, recomputes the
fingerprint of every response and compares it with the `id` inside. If the
two versions diverged, the two databases would not recognise each other:
this test is the reason why one can say "faithful port" without taking it
on trust.

It is run from `py_od`:

    python tools/check_fingerprint.py ..\\rust_od\\data\\od.db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from actions.decode import fingerprint  # noqa: E402
from actions.decrypt import decrypt_response, DecryptError  # noqa: E402


def main(argv: list[str]) -> int:
    database = Path(argv[1] if len(argv) > 1 else "../rust_od/data/od.db")

    if not database.exists():
        print(f"❌ database not found: {database}")

        return 1

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

    rows = connection.execute(
        "SELECT id, endpoint, iv, data, tag FROM responses ORDER BY endpoint"
    ).fetchall()

    connection.close()

    print(f"responses to check: {len(rows)}")
    print("")

    wrong_id = 0
    wrong_value = 0
    undecryptable = 0

    by_endpoint: dict[str, int] = {}

    for row_id, endpoint, iv, data, tag in rows:
        by_endpoint[endpoint] = by_endpoint.get(endpoint, 0) + 1

        # 1. The fingerprint recomputed in Python must be Rust's id.
        if fingerprint(iv, data, tag) != row_id:
            wrong_id += 1

            if wrong_id <= 3:
                print(f"   ❌ different fingerprint for {endpoint}:")
                print(f"      Rust:   {row_id}")
                print(f"      Python: {fingerprint(iv, data, tag)}")

        # 2. And the body must decrypt, with the right decimals.
        try:
            payload = decrypt_response(data, iv, tag)
        except DecryptError as error:
            undecryptable += 1

            if undecryptable <= 3:
                print(f"   ❌ it does not decrypt ({endpoint}): {error}")

            continue

        if not isinstance(payload, (dict, list)):
            wrong_value += 1

    print("responses per endpoint:")
    for endpoint, count in sorted(by_endpoint.items()):
        print(f"   {count:>4}  {endpoint}")

    print("")
    print(f"differing fingerprints:  {wrong_id}")
    print(f"unreadable payloads:     {wrong_value}")
    print(f"undecryptable responses: {undecryptable}")

    ok = wrong_id == 0 and wrong_value == 0 and undecryptable == 0

    print("")
    print("✅ Python and Rust compute the same fingerprint." if ok else "❌ the two versions diverge.")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
