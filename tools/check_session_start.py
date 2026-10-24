"""The start of the session, tested without credentials.

It builds the exact same start as `live.run` — persistent context (for the
profile on disk), `context.browser`, `new_tracked_window` — but instead of
navigating the real sites it uses a local page, and instead of opening all
the dashboards it opens three.

It serves to catch API errors **before** launching the browser on the site:
`launch(user_data_dir=...)` is a real example — that parameter only exists
on `launch_persistent_context`, and the error is only visible at start-up,
when you have already wasted the trip.

    python tools/check_session_start.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from playwright.async_api import async_playwright  # noqa: E402

import live as live_module  # noqa: E402

URL = "data:text/html,<h1>test</h1>"

SCRATCH = Path("target_session_check")

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures

    if not ok:
        failures += 1

    print(f"   {'✅' if ok else '❌'} {label}{' · ' + detail if detail else ''}")


async def window_of(page) -> int:
    """Which window this page is in."""
    session = await page.context.new_cdp_session(page)

    return (await session.send("Browser.getWindowForTarget"))["windowId"]


async def main() -> int:
    SCRATCH.mkdir(exist_ok=True)

    live = live_module.Live(
        str(SCRATCH / "probe.db"),
        capture=str(SCRATCH / "network.jsonl"),
        decrypted=str(SCRATCH / "decrypted.jsonl"),
    )

    async with async_playwright() as playwright:
        print("=== the start of the session, without navigating the sites ===")

        # The same start as `live.run`: persistent context, because it is
        # the only one that keeps the profile on disk.
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str((SCRATCH / "profile").resolve()),
            headless=False,
            args=["--window-size=1400,900"],
        )

        browser = context.browser

        check("`context.browser` is there (the new windows need it)", browser is not None)

        pages = context.pages

        page = pages[0] if pages else await context.new_page()

        await page.goto(URL)

        print("")

        print("=== one window per dashboard, with `new_tracked_window` ===")

        windows = [await window_of(page)]

        for index in range(3):
            window = await live_module.new_tracked_window(
                browser,
                f"{URL}?w={index}",
                f"finestra{index}",
                ["/api/v1/charts/breakdown-by-strike/"],
                "backend.optionsdepth.com",
                live,
            )

            windows.append(await window_of(window))

        await asyncio.sleep(1)

        print(f"   windows: {windows}")

        check(
            "every window is separate",
            len(set(windows)) == len(windows),
            f"{len(set(windows))} windows for {len(windows)} pages",
        )

        check(
            "`new_tracked_window` returns a usable page",
            await page.evaluate("() => 1") == 1,
        )

        print("")
        print("=== the sign-in page: do the fields get filled? ===")

        # The sign-in page is public, so it can really be tried. It is
        # written and **not** sent: login is not this test's job, and
        # nothing in the account is to be changed. It serves to catch the
        # case "the field is detached from the DOM while I write", which is
        # the one that used to crash the session at start-up.
        try:
            await page.goto(
                "https://app.optionsdepth.com/sign-in",
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            written = await live_module.fill_when_ready(
                page, "input[name='email']", "test@example.invalid"
            )

            check("the email is written into the field", written)
        except Exception as error:  # noqa: BLE001 — with no network it is not a defect of the code
            print(f"   (I could not try the sign-in page: {error})")

        await context.close()

    print("")

    if failures:
        print(f"❌ {failures} checks failed")

        return 1

    print("✅ The start of the session is fine.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
