"""RustOD in Python — the same thing, another language.

Usage:

    python main.py                     live capture (browser + database)
    python main.py targets             what would be opened and listened to
    python main.py decrypt             the captures of data/ → data/decrypted.jsonl
    python main.py decrypt file.jsonl  only the files given
    python main.py load                the decrypted payloads → database
    python main.py verify              checks the database (tools/verify_store.py)

The program expects to be launched from the project folder:
`config.json` and `data/` live there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The modules live in `src/`, like the `mod`s of Rust: whoever launches the
# script from the project folder must not install anything.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from actions import decode as decode_action  # noqa: E402
from actions.decrypt import DecryptError  # noqa: E402
from api import endpoints  # noqa: E402
from config import CONFIG_FILE, Config  # noqa: E402

CAPTURE_FILE = "data/network.jsonl"
DECRYPTED_FILE = "data/decrypted.jsonl"
DEFAULT_DATABASE = "data/od.db"


# ======================================================
# THE CAPTURE FILES
# ======================================================


def capture_files() -> list[str]:
    """The captures present in `data/`.

    `data/network.jsonl` and the sessions archived as `network-*.jsonl`,
    in name order.
    """
    folder = Path("data")

    if not folder.is_dir():
        return []

    files = [str(path) for path in sorted(folder.glob("network*.jsonl"))]

    return files


def given_or_all(arguments: list[str]) -> list[str]:
    """The files given on the command line, or all of those in `data/`."""
    if arguments:
        return arguments

    return capture_files()


# ======================================================
# DECRYPT
# ======================================================


def decrypt_file(path: str, output: str) -> tuple[int, int, int, int]:
    """Decrypts a capture file and writes the readable records.

    Returns `(read, decrypted, not_needed, errors)`.
    """
    read = 0
    decrypted = 0
    not_needed = 0
    errors = 0

    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()

    out = Path(output)

    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("a", encoding="utf-8") as writer:
        for line in lines:
            line = line.strip()

            if not line:
                continue

            # A line of the capture can hold more than one record: the
            # concurrent appends have no separator. It is split at the
            # start of the next record.
            for chunk in _split_records(line):
                read += 1

                try:
                    record = json.loads(chunk)
                except json.JSONDecodeError as error:
                    errors += 1

                    print(f"⚠️ unreadable record: {error}")

                    continue

                url = record.get("url") or ""

                if not endpoints.detect(url).is_needed():
                    not_needed += 1

                    continue

                try:
                    decoded = decode_action.decode(record)
                except DecodeError as error:
                    errors += 1

                    print(f"⚠️ decryption failed: {error}")

                    continue

                writer.write(
                    json.dumps(decoded.record(), ensure_ascii=False, separators=(",", ":")) + "\n"
                )

                print(decoded.line())

                decrypted += 1

    return read, decrypted, not_needed, errors


def _split_records(line: str) -> list[str]:
    """Splits a line that holds several JSON records stuck together."""
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


def command_decrypt(arguments: list[str]) -> int:
    files = given_or_all(arguments)

    if not files:
        print(f"❌ No capture to decrypt in data/ (looking for {CAPTURE_FILE}).")

        return 1

    print("========================================")
    print("🔓 RustOD DECRYPT MODE")
    print("========================================")
    print("")

    print(f"📄 Capture files: {len(files)}")

    for path in files:
        print(f"   {path}")

    print(f"🔓 Output: {DECRYPTED_FILE}")
    print("")

    # It is rewritten from scratch: the file is derived, not an archive.
    Path(DECRYPTED_FILE).unlink(missing_ok=True)

    read = decrypted = not_needed = errors = 0

    for path in files:
        before = decrypted

        try:
            one_read, one_decrypted, one_not_needed, one_errors = decrypt_file(
                path, DECRYPTED_FILE
            )
        except OSError as error:
            print(f"   ❌ {path}: {error}")

            errors += 1

            continue

        read += one_read
        decrypted += one_decrypted
        not_needed += one_not_needed
        errors += one_errors

        print(f"   ✔ {path:<40} {decrypted - before} decrypted")

    print("")
    print("========================================")
    print("✅ DECRYPT COMPLETED")
    print("========================================")
    print(f"📄 Records read:       {read}")
    print(f"🔓 Decrypted:          {decrypted}")
    print(f"⏭️ Not needed:         {not_needed}")
    print(f"❌ Errors:             {errors}")
    print("")
    print(f"📄 Readable payloads: {DECRYPTED_FILE}")

    return 0 if errors == 0 else 1


# ======================================================
# LOAD
# ======================================================


def command_load(arguments: list[str]) -> int:
    from store import StoreError, load_file, open_database

    source = arguments[0] if arguments else DECRYPTED_FILE
    database = arguments[1] if len(arguments) > 1 else DEFAULT_DATABASE

    print("========================================")
    print("    OptionsDepth Load Mode")
    print("========================================")
    print("")
    print(f"    Payload decrypted: {source}")
    print(f"    Database: {database}")
    print("")

    if not Path(source).exists():
        print(f"❌ There is no {source}: there is nothing to load.")
        print("   The database is filled by the capture, while it is open (`python main.py`).")
        print("   If you have a capture set aside: `python main.py decrypt` and then `load` again.")

        return 1

    connection = open_database(database)

    try:
        stats = load_file(connection, source)
    except StoreError as error:
        print(f"❌ {error}")

        return 1

    _print_load_report(connection, stats)

    return 0 if not stats.problems else 1


def _print_load_report(connection, stats) -> None:
    print("========================================")
    print("✅ LOAD COMPLETED")
    print("========================================")
    print(f"    Responses read:       {stats.records}")
    print(f"    Loaded:               {stats.loaded}")
    print(f"    Already done:         {stats.skipped}")
    print(f"    Rows written:         {stats.rows}")
    print(f"    Errors:               {len(stats.problems)}")
    print("")

    for problem in stats.problems[:20]:
        print(f"    ⚠️ {problem}")

    if len(stats.problems) > 20:
        print(f"    … and {len(stats.problems) - 20} more")

    print("")

    _print_database_report(connection)


def _print_database_report(connection) -> None:
    """What is in the database now, table by table."""
    from store import (
        candle_coverage,
        coverage,
        depth_coverage,
        expiration_coverage,
        heatmap_coverage,
        slice_coverage,
        surface_coverage,
        volatility_coverage,
    )

    total = connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

    print(f"🗄️ Responses in the archive: {total}")
    print("")

    sections = [
        ("Breakdown by strike", "breakdown_by_strike", coverage),
        ("Breakdown by expiration", "breakdown_by_expiration", expiration_coverage),
        ("Depth view", "depth_view", depth_coverage),
        ("Heatmaps", "heatmaps", heatmap_coverage),
        ("Volatility table", "volatility_table", volatility_coverage),
        ("Volatility surface", "volatility_surface", surface_coverage),
        ("Volatility slices", "volatility_slices", slice_coverage),
        ("Candlesticks", "candlesticks", candle_coverage),
    ]

    for title, table, function in sections:
        rows = function(connection)

        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        print(f"📊 {title}: {count} rows in {len(rows)} series")

    print("")


# ======================================================
# TARGETS
# ======================================================


def command_targets() -> int:
    config = Config.load()

    print("========================================")
    print("🎯 TO LISTEN TO (targets in config.json)")
    print("========================================")
    print("")

    print(f"📊 DASHBOARD ({len(config.dashboard)})")

    for key, dashboard in config.dashboard.items():
        print(f"   {key:<22} name=\"{dashboard.name}\"")

        for target in dashboard.targets:
            print(f"      → {target}")

    print("")
    print(f"🌐 PAGES ({len(config.pages)})")

    for key, page in config.pages.items():
        suffix = "   ⏭️ (presses the button that moves time forward)" if page.next else ""

        print(f"   {key:<22} {page.url}{suffix}")

        for target in page.targets:
            print(f"      → {target}")

    return 0


# ======================================================
# THE COMMAND LINE
# ======================================================


def main(argv: list[str]) -> int:
    arguments = [argument.lstrip("-") for argument in argv[1:]]

    command = arguments[0] if arguments else "live"

    rest = arguments[1:]

    if command == "targets":
        return command_targets()

    if command == "decrypt":
        return command_decrypt(rest)

    if command == "load":
        return command_load(rest)

    if command == "verify":
        # The verifier lives in `tools/`, as in the Rust project: it is a
        # tool, not part of the program. It is launched as a separate
        # process, so it can be used on its own too.
        import subprocess

        script = Path(__file__).resolve().parent / "tools" / "verify_store.py"

        return subprocess.call(
            [sys.executable, str(script), "--db", DEFAULT_DATABASE, *rest]
        )

    if command == "inspect":
        from inspect_payloads import SCHEMA_REPORT, inspect_files

        files = given_or_all(rest)

        if not files:
            print("❌ No capture to inspect in data/.")

            return 1

        print(f"🔎 Inspecting {len(files)} files…")

        report = inspect_files(files)

        print(
            f"   records: {report['records']} · decrypted: {report['decrypted']} · "
            f"errors: {report['errors']}"
        )
        print("")

        for name, entry in sorted(report["endpoints"].items()):
            print(f"📊 {entry['display']} ({name}): {entry['requests']} requests")

        print("")
        print(f"📄 Report: {SCHEMA_REPORT}")

        return 0

    if command == "probe":
        from inspect_payloads import probe_files

        if not rest:
            print("❌ The endpoint to look at is required: python main.py probe strike 1")
            print("   (or `all` for all of them)")

            return 1

        wanted = rest[0]

        limit = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 1

        files = given_or_all(rest[2:]) or capture_files()

        if not files:
            print("❌ No capture in data/.")

            return 1

        return probe_files(files, wanted, limit)

    if command in ("live", "run"):
        from live import run_session

        return run_session()

    print(f"❌ Unknown command: {command}")
    print("")
    print("   python main.py            live capture")
    print("   python main.py targets    what would be listened to")
    print("   python main.py decrypt    the captures → data/decrypted.jsonl")
    print("   python main.py load       the decrypted payloads → database")
    print("   python main.py verify     checks the database")
    print("   python main.py inspect    the shape of the payloads → data/schema_report.json")
    print("   python main.py probe <endpoint> <n>   the real values of n samples")

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
