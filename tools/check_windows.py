"""Do the pages open in separate windows?

The code is not inspected: a real Chromium is opened, the windows are
created the way the session does, and Chromium is asked **which window**
every page is in (`Browser.getWindowForTarget`). Pages in different windows
= one window per page; pages all in the same one = tabs.

    python tools/check_windows.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from playwright.async_api import async_playwright  # noqa: E402

URL = "data:text/html,<h1>test</h1>"

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures

    if not ok:
        failures += 1

    print(f"   {'✅' if ok else '❌'} {label}{' · ' + detail if detail else ''}")


async def window_of(page) -> int:
    """Which window this page is in."""
    session = await page.context.browser.new_browser_cdp_session()

    # The target of the page: it is taken from its own CDP.
    own = await page.context.new_cdp_session(page)

    answer = await own.send("Browser.getWindowForTarget")

    return answer["windowId"]


async def main() -> int:
    from playwright.async_api import async_playwright as starter  # noqa: F401

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)

        # It reproduces **exactly** what `live.new_tracked_window` does:
        # one context per window, one page per context.
        print("=== three windows, the way the session opens them ===")

        pages = []

        for index in range(3):
            context = await browser.new_context()

            page = await context.new_page()

            await page.goto(f"{URL}?n={index}")

            pages.append(page)

        await asyncio.sleep(1)

        windows = [await window_of(page) for page in pages]

        print(f"   windows: {windows}")

        check(
            "one window per page",
            len(set(windows)) == len(pages),
            f"{len(set(windows))} windows for {len(pages)} pages",
        )

        check("the context is one per window", len(browser.contexts) == 3, f"{len(browser.contexts)} contexts")

        # And do the pages answer?
        title = await pages[1].evaluate("() => document.querySelector('h1')?.textContent")

        check("the pages can be used", title == "test", f"the second one says «{title}»")

        # The opposite, to show that the measurement really does tell them
        # apart: three pages in the **same** context must end up in one
        # window.
        print("")
        print("=== counter-check: three pages in the same context ===")

        context = await browser.new_context()

        same = []

        for index in range(3):
            page = await context.new_page()

            await page.goto(f"{URL}?s={index}")

            same.append(page)

        await asyncio.sleep(1)

        windows = [await window_of(page) for page in same]

        print(f"   windows: {windows}")

        check(
            "the measurement tells them apart: here it is a single window",
            len(set(windows)) == 1,
            f"{len(set(windows))} windows for {len(pages)} pages",
        )

        await browser.close()

    print("")

    if failures:
        print(f"❌ {failures} checks failed")

        return 1

    print("✅ The pages open in separate windows.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
