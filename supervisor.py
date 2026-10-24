"""Runs the scraper around the US market session, and clears its data after.

Launched once (Task Scheduler, at logon — see `deploy/`) and left
running, it loops forever:

    sleep until (market open − 3 min)
        → `python main.py live`
    sleep until market close
        → stop it cleanly (the same path as Ctrl+C, see `live.STOP_FLAG`)
        → wipe data/network.jsonl, data/decrypted.jsonl, data/od.db
    repeat, next trading day

## Why the schedule is computed here, not in Task Scheduler

The open and close are fixed in **America/New_York**, which alternates
between EST and EDT. A Task Scheduler trigger has no notion of that: it
either drifts by an hour twice a year, or forces the whole VPS onto
Eastern time. `zoneinfo` carries the real DST rules, so a plain "9:30
local" computed against `America/New_York` is correct year-round — the
one timezone-aware moving part lives in Python, where it can be read and
tested.

## Why the data is wiped after every session

One session is sized at ~5 GB (estimated from the capture so far — most
of it `data/decrypted.jsonl`, which is append-only and never trimmed,
plus `data/od.db` growing with every new response). Until the project
has a real retention strategy (downsampling, export, …), keeping that
daily on a VPS is how the disk fills up unattended. So each day starts
from an empty database — `store.open_database` recreates the schema on
the next run, same as on a machine that never had one.

## Known gap: US market holidays

This only skips Saturday and Sunday. On a holiday the scraper will
still start, find nothing useful, and run for nothing — annoying, not
harmful (no data to lose, nothing to overflow). Worth a NYSE holiday
calendar later; not before it has mattered once.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

MARKET_TZ = ZoneInfo("America/New_York")

MARKET_OPEN = (9, 30)
MARKET_CLOSE = (16, 0)
START_LEAD = timedelta(minutes=3)

# Between sessions, checked every this many seconds while sleeping — short
# enough that Ctrl+C on the supervisor itself does not hang for long.
POLL_SECONDS = 5

PROJECT_ROOT = Path(__file__).resolve().parent
STOP_FLAG = PROJECT_ROOT / "data" / ".stop"

# What one session leaves behind, relative to the project root.
SESSION_FILES = (
    "data/network.jsonl",
    "data/decrypted.jsonl",
    "data/od.db",
    "data/od.db-wal",
    "data/od.db-shm",
)


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MARKET_TZ)


def next_session_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The (start, stop) of the next session that has not finished yet.

    `start` already has the 3-minute lead subtracted. Weekends are
    skipped; so is the rest of a day whose close has already passed.
    """
    local_now = now.astimezone(MARKET_TZ)
    day = local_now.date()

    while True:
        if day.weekday() < 5:  # Monday .. Friday
            close_at = _at(day, *MARKET_CLOSE)

            if close_at > local_now:
                open_at = _at(day, *MARKET_OPEN)

                return open_at - START_LEAD, close_at

        day += timedelta(days=1)


def sleep_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(MARKET_TZ)).total_seconds()

        if remaining <= 0:
            return

        time.sleep(min(POLL_SECONDS, remaining))


def wipe_session_data() -> None:
    """Deletes the files one session leaves behind.

    On Windows, a file still held open by another process cannot be
    deleted (confirmed by testing: `PermissionError: [WinError 32]`).
    The API server (`server/`) only ever holds a connection open for the
    lifetime of one request (see `server/db.py`'s docstring for why it
    is deliberately not pooled), so the window where this could race a
    delete is a few milliseconds — but it is a real window on a server
    getting polled continuously, so each file gets a few retries instead
    of failing the whole session on a single unlucky request.
    """
    for relative in SESSION_FILES:
        path = PROJECT_ROOT / relative

        if not path.exists():
            continue

        for attempt in range(5):
            try:
                path.unlink()

                print(f"🗑️ removed {relative}")

                break
            except OSError as error:
                if attempt == 4:
                    print(f"⚠️ could not remove {relative} after 5 tries: {error}")

                    break

                time.sleep(0.5)


def run_scraper_until(stop_at: datetime) -> None:
    """Starts `main.py live` and stops it when `stop_at` is reached."""
    STOP_FLAG.unlink(missing_ok=True)

    process = subprocess.Popen(
        [sys.executable, "main.py", "live"],
        cwd=PROJECT_ROOT,
    )

    print(f"🚀 scraper started (pid {process.pid}) — stopping at {stop_at.isoformat()}")

    while datetime.now(MARKET_TZ) < stop_at:
        if process.poll() is not None:
            print(f"⚠️ scraper exited on its own (code {process.returncode}), before market close")

            return

        time.sleep(POLL_SECONDS)

    print("🛑 market closed: asking the scraper to stop")

    STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
    STOP_FLAG.touch()

    try:
        process.wait(timeout=60)

        print(f"✅ scraper stopped cleanly (code {process.returncode})")
    except subprocess.TimeoutExpired:
        print("⚠️ the scraper did not stop in time: terminating it")

        process.terminate()

        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> None:
    print(f"🕒 supervisor started — market hours: {MARKET_OPEN} - {MARKET_CLOSE} America/New_York")

    while True:
        start_at, stop_at = next_session_bounds(datetime.now(MARKET_TZ))

        print(f"😴 next session: {start_at.isoformat()} → {stop_at.isoformat()}")

        sleep_until(start_at)

        run_scraper_until(stop_at)

        wipe_session_data()

        print("✅ session data cleared — waiting for the next trading day")
        print("")

        # Past `stop_at` already: this just avoids re-deriving the same
        # session twice in a tight loop.
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("")
        print("🛑 Supervisor interrupted.")
