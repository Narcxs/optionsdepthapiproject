"""Does the config rewrite itself without losing fields?

`set_active` rewrites the file: if it lost something, the program would
wipe the user's configuration at the first start. It is tried on a
**copy**, never on the real file.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from config import Config  # noqa: E402

source = Path("config.json")
copy = Path("target_config_check.json")

shutil.copyfile(source, copy)

before = json.loads(copy.read_text(encoding="utf-8"))

config = Config.from_content(str(copy), copy.read_text(encoding="utf-8"))

config.set_active("dashboard", "Heatmaps", True)
config.set_active("pages", "Volatility", True)

after = json.loads(copy.read_text(encoding="utf-8"))

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures

    if not ok:
        failures += 1

    print(f"   {'✅' if ok else '❌'} {label}{' · ' + detail if detail else ''}")


print("=== after a rewrite ===")

check("the credentials are still there", after.get("email_od") == before.get("email_od"))

check("the backend is still there", after.get("backend") == before.get("backend"))

check(
    "all the top-level keys are still there",
    set(after) == set(before),
    f"{sorted(set(before) - set(after))} lost",
)

check(
    "the dashboards are still four",
    set(after["dashboard"]) == set(before["dashboard"]),
)

check(
    "the pages are still two",
    set(after["pages"]) == set(before["pages"]),
)

check("the active flag written is true", after["dashboard"]["Heatmaps"]["active"] is True)

check("and the other one too", after["pages"]["Volatility"]["active"] is True)

check(
    "the targets have not changed",
    after["dashboard"]["Heatmaps"]["targets"] == before["dashboard"]["Heatmaps"]["targets"],
)

check(
    "the `next` field has not disappeared",
    after["pages"]["Surface"].get("next") is True,
)

# Reading the rewritten file again must give the same state.
reread = Config.from_content(str(copy), copy.read_text(encoding="utf-8"))

check(
    "reading it again finds the state that was written",
    ("Heatmaps", True) in reread.previous_state(),
    f"{reread.previous_state()[:2]}…",
)

# And the rewrite keeps the four-space indentation.
text = copy.read_text(encoding="utf-8")

check("the indentation is four spaces", '\n    "url_od"' in text)

copy.unlink(missing_ok=True)

print("")

if failures:
    print(f"❌ {failures} checks failed")

    raise SystemExit(1)

print("✅ The config rewrites itself without losing anything.")
