"""Uno legge mentre l'altro scrive: si disturbano?

Due processi veri, in parallelo:

  · lo **scrittore** scrive risposte di prova senza fermarsi;
  · il **lettore** legge, e misura quanto ci mette e se trova errori.

È la domanda che decide se l'API può stare in un processo suo.

    python tools/check_concurrent_read.py
"""

from __future__ import annotations

import json
import multiprocessing
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

SCRATCH = Path("target_concurrent")

URL = (
    "https://backend.optionsdepth.com/api/v1/charts/breakdown-by-strike/"
    "?date=2026-10-01&ticker=SPX&date_time=2026-10-01T15:{minute:02d}:00"
    "&participants=mm_posn&metric=GEX"
)


def make_record(index: int) -> dict:
    """Una risposta di breakdown vera, con un'impronta diversa ogni volta."""
    payload = {
        "figure": {
            "data": [
                {"name": "Calls", "x": [1.0], "y": [7600.0], "customdata": [[1.0, 2.0, 3.0]]},
                {"name": "Puts", "x": [2.0], "y": [7600.0]},
                {"name": "Net", "x": [3.0], "y": [7600.0]},
            ]
        }
    }

    # Non serve cifrare: si scrive direttamente nel database, che è la cosa
    # che si vuole misurare.
    return {
        "captured_at": 1790768577 + index,
        "received_at": "2026-10-01 19:27:17",
        "endpoint": "breakdown-by-strike",
        "url": URL.format(minute=index % 60),
        "fingerprint": f"{index:016x}",
        "iv": "aXY",
        "data": f"ZGF0YQ{index}",
        "tag": "dGFn",
        "payload": payload,
    }


def writer(database: str, how_many: int, started: multiprocessing.Event, done: multiprocessing.Event) -> None:
    """Writes without stopping, and counts the errors.

    It writes into a database of its own unless one is given: writing into
    the real archive would add responses that never existed. To try it on the
    real archive (read-only for the reader, which is the interesting half):

        python tools/check_concurrent.py --real
    """
    import store

    connection = store.open_database(database)

    errors: list[str] = []
    written = 0

    started.set()

    for index in range(how_many):
        try:
            store.store_record(connection, make_record(index))

            written += 1
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))

    connection.close()

    Path(database + ".writer").write_text(
        json.dumps({"written": written, "errors": errors[:20], "error_count": len(errors)}),
        encoding="utf-8",
    )

    done.set()


def reader(database: str, started: multiprocessing.Event, done: multiprocessing.Event) -> None:
    """Legge di continuo, e misura."""
    time.sleep(1.5)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

    reads = 0
    errors: list[str] = []
    worst = 0.0
    total = 0.0
    rows_seen = 0

    while not (done.is_set() and reads > 40):
        began = time.monotonic()

        try:
            count = connection.execute("SELECT COUNT(*) FROM breakdown_by_strike").fetchone()[0]

            rows_seen = max(rows_seen, count)
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
        finally:
            elapsed = time.monotonic() - began

            worst = max(worst, elapsed)
            total += elapsed
            reads += 1

        time.sleep(0.01)

    connection.close()

    Path(database + ".reader").write_text(
        json.dumps(
            {
                "reads": reads,
                "errors": errors[:20],
                "error_count": len(errors),
                "worst_ms": round(worst * 1000, 2),
                "mean_ms": round((total / reads) * 1000, 3) if reads else 0,
                "rows_seen": rows_seen,
            }
        ),
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    import store

    # `--real`: si copia l'archivio vero e si scrive nella copia, mentre il
    # lettore legge. Serve a misurare su dati veri invece che su un database
    # vuoto — e a non sporcare l'archivio con risposte che non sono mai
    # arrivate.
    real = "--real" in argv

    shutil.rmtree(SCRATCH, ignore_errors=True)

    SCRATCH.mkdir(parents=True)

    database = str(SCRATCH / "concurrent.db")

    if real:
        source = Path("data/od.db")

        if not source.exists():
            print(f"❌ {source} non c'è")

            return 1

        shutil.copyfile(source, database)

        print(f"archivio vero copiato: {source} ({source.stat().st_size / 1e6:.0f} MB)")

        rows = sqlite3.connect(f"file:{database}?mode=ro", uri=True).execute(
            "SELECT COUNT(*) FROM breakdown_by_strike"
        ).fetchone()[0]

        print(f"   {rows} righe di breakdown by strike da leggere")
    else:
        # Il database si crea prima, così i due processi lo trovano pronto.
        connection = store.open_database(database)

        connection.close()

    started = multiprocessing.Event()
    done = multiprocessing.Event()

    how_many = 400

    print(f"scrittore: {how_many} risposte · lettore: legge intanto")

    process_writer = multiprocessing.Process(target=writer, args=(database, how_many, started, done))

    process_reader = multiprocessing.Process(target=reader, args=(database, started, done))

    process_reader.start()
    process_writer.start()

    process_writer.join()
    process_reader.join()

    report_writer = json.loads(Path(database + ".writer").read_text(encoding="utf-8"))
    report_reader = json.loads(Path(database + ".reader").read_text(encoding="utf-8"))

    print("")
    print("=== lo scrittore ===")
    print(f"   risposte scritte: {report_writer['written']} su {how_many}")
    print(f"   errori:           {report_writer['error_count']}")

    for error in report_writer["errors"][:5]:
        print(f"      {error}")

    print("")
    print("=== il lettore ===")
    print(f"   letture:          {report_reader['reads']}")
    print(f"   errori:           {report_reader['error_count']}")
    print(f"   più lenta:        {report_reader['worst_ms']} ms")
    print(f"   media:            {report_reader['mean_ms']} ms")
    print(f"   righe viste:      {report_reader['rows_seen']} (cresce mentre scrive)")

    for error in report_reader["errors"][:5]:
        print(f"      {error}")

    print("")

    failures = 0

    if report_writer["error_count"]:
        print("❌ lo scrittore ha trovato errori")

        failures += 1

    if report_reader["error_count"]:
        print("❌ il lettore ha trovato errori")

        failures += 1

    if not failures:
        print("✅ Si scrive e si legge insieme, senza che nessuno dei due si fermi.")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
