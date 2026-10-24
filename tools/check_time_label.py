"""L'etichetta della console: dichiarata o dedotta?

Si costruiscono risposte vere (cifrate e decifrate) — una con `date_time`
nella URL, una senza — e si guarda cosa scrive la console. È il difetto che
faceva sembrare il programma fermo: sette risposte dello **stesso** istante,
sette volte lo stesso minuto, senza modo di sapere che erano sette.

    python tools/check_time_label.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from actions import decode  # noqa: E402
from actions.decrypt import encrypt_for_test  # noqa: E402

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures

    if not ok:
        failures += 1

    print(f"   {'✅' if ok else '❌'} {label}{' · ' + detail if detail else ''}")


BASE = "https://backend.optionsdepth.com/api/v1/charts/breakdown-by-strike/"

WHEN = 1791131700  # un istante di arrivo fisso, così il confronto è onesto


def response(url: str) -> decode.Decoded:
    """Una risposta vera: cifrata con la chiave del progetto, poi decifrata."""
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

    return decode.decode(
        {
            "timestamp": WHEN,
            "url": url,
            "method": "GET",
            "body": {"data": data, "iv": iv, "tag": tag},
        }
    )


WITH = (
    BASE + "?date=2026-10-02&ticker=SPX&participants=mm_posn&metric=GEX"
    "&date_time=2026-10-02T17:00:00"
)

WITHOUT = BASE + "?date=2026-10-05&ticker=SPX&participants=mm_posn&metric=GEX"

print("=== l'istante dichiarato dalla URL ===")

line = response(WITH).line()

print(f"   {line}")

check("mostra l'istante del grafico", "17:00" in line, line.split("2026")[0][-30:])

check("e non porta il segno ≈", "≈" not in line)

print("")
print("=== l'istante dedotto dall'arrivo ===")

line = response(WITHOUT).line()

print(f"   {line}")

check("porta il segno ≈: non è dichiarato, è dedotto", "≈" in line, line.split("≈")[1][:12])

check("e non inventa un istante del grafico", "17:00" not in line)

print("")
print("=== sette serie dello stesso istante: si distinguono? ===")

built = []

for metric in ("GEX", "DEX", "CEX", "VEX", "NET_POSITION"):
    for side in ("mm_posn", "customer_posn"):
        decoded = response(
            BASE + f"?date=2026-10-05&ticker=SPX&participants={side}&metric={metric}"
        )

        built.append((decoded.label(), decoded.dimensions.time_label(decoded.captured_at)))

for label, moment in built[:3]:
    print(f"   📥 {label} · {moment}")

print(f"   … e altre {len(built) - 3}")

# I pezzi, non la riga stampata: il nome contiene anch'esso dei `·`, quindi
# spezzare la stringa per `·` misura la cosa sbagliata. (È l'errore che
# faceva fallire questa prova alla prima scrittura.)
check("tutte segnate come dedotte", all("≈" in moment for _, moment in built))

check(
    "stesso istante (sono lo stesso momento) ma nomi diversi",
    len({moment for _, moment in built}) == 1 and len({label for label, _ in built}) == len(built),
    f"{len({moment for _, moment in built})} istante · "
    f"{len({label for label, _ in built})} nomi su {len(built)} risposte",
)

print("")

if failures:
    print(f"❌ {failures} controlli falliti")

    sys.exit(1)

print("✅ L'etichetta dice il vero: dichiarata quando c'è, dedotta (e segnata) quando no.")
