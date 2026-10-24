"""I dati possono finire due volte nel database?

Tre strade diverse, provate una per una:

  A. lo stesso file di decifrati caricato due volte (`load` due volte);
  B. la stessa risposta scritta due volte di fila (`store_record` due volte);
  C. la stessa risposta ricaricata in una **seconda** sessione, con il
     database riaperto — cioè il caso vero: la cattura dal vivo e poi un
     `load`, o due `load` a distanza di giorni.

E una quarta cosa, che è quella che ti interessa: una risposta di candele
**vuota** (mercato chiuso) non deve lasciare righe, e non deve impedire
che la stessa risposta, arrivata di nuovo, venga riconosciuta.

    python tools/check_no_duplicates.py
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from actions.decrypt import encrypt_for_test  # noqa: E402
import store  # noqa: E402

SCRATCH = Path("target_duplicates")

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures

    if not ok:
        failures += 1

    print(f"   {'✅' if ok else '❌'} {label}{' · ' + detail if detail else ''}")


def counts(database: Path) -> dict[str, int]:
    """Quante righe ha ogni tabella."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

    tables = [
        "responses",
        "breakdown_by_strike",
        "breakdown_by_expiration",
        "depth_view",
        "heatmaps",
        "volatility_table",
        "volatility_surface",
        "volatility_slices",
        "candlesticks",
    ]

    result = {}

    for table in tables:
        result[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    connection.close()

    return result


def main() -> int:
    shutil.rmtree(SCRATCH, ignore_errors=True)

    SCRATCH.mkdir(parents=True)

    source = Path("data/decrypted.jsonl")

    if not source.exists():
        print("❌ serve data/decrypted.jsonl: lancia prima `python main.py decrypt`")

        return 1

    records = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]

    print(f"file di partenza: {len(records)} record")
    print("")

    # ================================================================
    print("=== A. lo stesso file caricato due volte ===")

    database = SCRATCH / "a.db"

    connection = store.open_database(str(database))

    first = store.load_file(connection, str(source))

    after_first = counts(database)

    second = store.load_file(connection, str(source))

    after_second = counts(database)

    connection.close()

    print(f"   primo load:    {first.loaded} scritti, {first.skipped} già presenti, {first.rows} righe")
    print(f"   secondo load:  {second.loaded} scritti, {second.skipped} già presenti, {second.rows} righe")

    check(
        "il secondo load non scrive niente",
        second.loaded == 0 and second.rows == 0,
        f"{second.loaded} scritti, {second.rows} righe",
    )

    check("il secondo load riconosce tutto", second.skipped == first.loaded + first.skipped)

    same = all(after_first[table] == after_second[table] for table in after_first)

    check(
        "le righe sono identiche dopo il secondo load",
        same,
        " · ".join(f"{t} {after_first[t]}→{after_second[t]}" for t in after_first if after_first[t] != after_second[t])
        or "nessuna tabella cambiata",
    )

    print("")

    # ================================================================
    print("=== B. la stessa risposta scritta due volte ===")

    database = SCRATCH / "b.db"

    connection = store.open_database(str(database))

    # Una risposta vera, presa dal file.
    record = None

    for line in records:
        candidate = json.loads(line)

        if candidate["endpoint"] == "breakdown-by-strike" and store.endpoint_of(candidate):
            record = candidate

            break

    if record is None:
        print("   ❌ non c'è una risposta di breakdown nel file")

        return 1

    stored_once = store.store_record(connection, record)

    before = counts(database)

    stored_twice = store.store_record(connection, record)

    after = counts(database)

    connection.close()

    check(
        "la prima scrittura scrive",
        isinstance(stored_once, store.New),
        f"{stored_once.rows if isinstance(stored_once, store.New) else '-'} righe",
    )

    check(
        "la seconda non scrive niente",
        isinstance(stored_twice, store.AlreadyThere),
        type(stored_twice).__name__,
    )

    check(
        "e le righe non cambiano",
        before == after,
        " · ".join(f"{t} {before[t]}→{after[t]}" for t in before if before[t] != after[t]) or "niente cambiato",
    )

    print("")

    # ================================================================
    print("=== C. riaperto in una seconda sessione ===")

    database = SCRATCH / "c.db"

    connection = store.open_database(str(database))

    store.load_file(connection, str(source))

    connection.close()

    # Sessione nuova, database riaperto da zero.
    connection = store.open_database(str(database))

    reopened = store.load_file(connection, str(source))

    connection.close()

    check(
        "riaprendo il database, il load non riscrive",
        reopened.loaded == 0 and reopened.rows == 0,
        f"{reopened.loaded} scritti, {reopened.rows} righe",
    )

    print("")

    # ================================================================
    print("=== D. una risposta di candele senza candele (mercato chiuso) ===")

    empty = [json.loads(line) for line in records if json.loads(line)["endpoint"] == "plotly-candlesticks"]

    without = [
        item
        for item in empty
        if not ((item.get("payload") or {}).get("figure", {}).get("data") or [])
    ]

    print(f"   risposte di candele: {len(empty)} · senza candele: {len(without)}")

    if not without:
        check("ce n'è almeno una vuota da provare", False, "nessuna risposta vuota nel file")

    else:
        database = SCRATCH / "d.db"

        connection = store.open_database(str(database))

        before = counts(database)

        for item in without:
            try:
                store.store_record(connection, item)
            except store.StoreError as error:
                print(f"   (rifiutata, come deve: {error})")

        after = counts(database)

        connection.close()

        check(
            "una risposta vuota non lascia righe",
            after["candlesticks"] == before["candlesticks"] == 0,
            f"candlesticks {before['candlesticks']}→{after['candlesticks']}",
        )

        check(
            "e non lascia nemmeno una risposta in archivio",
            after["responses"] == 0,
            f"{after['responses']} risposte",
        )

        # La stessa risposta, arrivata di nuovo più tardi: la si ritrova?
        # (Nel database non c'è, quindi non c'è niente da riconoscere: la
        # si riscrive, e va bene così — non ha mai prodotto righe.)
        database = SCRATCH / "d2.db"

        connection = store.open_database(str(database))

        # Prima una risposta **vera** di candele, poi la vuota: la vuota non
        # deve toccare le righe dell'altra.
        with_candles = None

        for item in empty:
            traces = (item.get("payload") or {}).get("figure", {}).get("data") or []

            if traces:
                with_candles = item

                break

        if with_candles is not None:
            store.store_record(connection, with_candles)

            rows_before = counts(database)["candlesticks"]

            for item in without:
                try:
                    store.store_record(connection, item)
                except store.StoreError:
                    pass

            rows_after = counts(database)["candlesticks"]

            connection.close()

            check(
                "la vuota non tocca le candele di un'altra risposta",
                rows_before == rows_after and rows_before > 0,
                f"{rows_before} → {rows_after}",
            )
        else:
            connection.close()

    print("")

    # ================================================================
    print("=== E. e nel database vero? (solo lettura) ===")

    real = Path("data/od.db")

    if real.exists():
        counts_now = counts(real)

        print(f"   {counts_now['responses']} risposte in archivio")

        # La prova che un doppione non c'è: nessuna risposta condivisa fra due
        # righe della stessa tabella sulla stessa chiave primaria. Se ci
        # fosse, SQLite non l'avrebbe nemmeno accettata — ma si controlla
        # anche che l'archivio non abbia due volte lo stesso body cifrato.
        connection = sqlite3.connect(f"file:{real}?mode=ro", uri=True)

        duplicates = connection.execute(
            "SELECT COUNT(*) FROM (SELECT iv, data, tag, COUNT(*) AS n FROM responses "
            "GROUP BY iv, data, tag HAVING n > 1)"
        ).fetchone()[0]

        repeated = connection.execute(
            "SELECT COUNT(*) FROM (SELECT id, COUNT(*) AS n FROM responses GROUP BY id HAVING n > 1)"
        ).fetchone()[0]

        # E: la stessa cella due volte nella stessa risposta?
        cell_duplicates = 0

        for table, columns in (
            ("breakdown_by_strike", "response_id, strike"),
            ("depth_view", "response_id, expiration, option_symbol, strike"),
            ("heatmaps", "response_id, time_slot, price"),
            ("candlesticks", "response_id, candle_time"),
            ("volatility_surface", "response_id, expiration, strike"),
        ):
            found = connection.execute(
                f"SELECT COUNT(*) FROM (SELECT {columns}, COUNT(*) AS n FROM {table} "
                f"GROUP BY {columns} HAVING n > 1)"
            ).fetchone()[0]

            cell_duplicates += found

        connection.close()

        check("nessun body cifrato ripetuto in archivio", duplicates == 0, f"{duplicates} ripetuti")

        check("nessun id ripetuto in archivio", repeated == 0, f"{repeated} ripetuti")

        check(
            "nessuna cella ripetuta nella stessa risposta",
            cell_duplicates == 0,
            f"{cell_duplicates} celle ripetute",
        )
    else:
        print("   (data/od.db non c'è: salto)")

    print("")

    if failures:
        print(f"❌ {failures} controlli falliti")

        return 1

    print("✅ Nessuna strada porta a un doppione.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
