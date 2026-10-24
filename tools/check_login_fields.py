"""Writing into the login fields, tried on the real page.

The sign-in page is public, so it can really be opened and written to: it
is the only way to know whether the field gets detached from the DOM and
whether the attempt that retries holds. **Log In is not pressed**: it is
written and that is all, so nothing in the account changes.

    python tools/check_login_fields.py
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

SIGN_IN = "https://app.optionsdepth.com/sign-in"

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures

    if not ok:
        failures += 1

    print(f"   {'✅' if ok else '❌'} {label}{' · ' + detail if detail else ''}")


async def main() -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)

        page = await browser.new_page()

        print(f"=== I open {SIGN_IN} ===")

        await page.goto(SIGN_IN, wait_until="domcontentloaded", timeout=60_000)

        print(f"   we are on: {page.url}")

        # The fields are not there straight away: the page is React and
        # draws them later (`domcontentloaded` arrives first). It waits for
        # them to appear — this is exactly why the write must retry instead
        # of giving up at the first attempt.
        appeared = 0

        for selector in ("input[name='email']", "input[name='password']"):
            try:
                await page.locator(selector).first.wait_for(state="visible", timeout=30_000)

                appeared += 1
            except Exception:  # noqa: BLE001
                pass

        check("the fields appear (after the wait)", appeared == 2, f"{appeared} of 2")

        for selector in ("input[name='email']", "input[name='password']"):
            count = await page.locator(selector).count()

            check(f"the field {selector} exists", count >= 1, f"{count} elements")

        print("")
        print("=== I write the email (without pressing Log In) ===")

        started = asyncio.get_running_loop().time()

        written = await live_module.fill_when_ready(
            page, "input[name='email']", "test@example.invalid"
        )

        seconds = asyncio.get_running_loop().time() - started

        check("the write succeeded", written, f"in {seconds:.1f} s")

        if written:
            value = await page.locator("input[name='email']").first.input_value()

            check("the value has stayed in the field", value == "test@example.invalid", f"«{value}»")

        print("")
        print("=== and the password ===")

        written = await live_module.fill_when_ready(page, "input[name='password']", "test")

        check("the write succeeded", written)

        print("")

        # Is the button there? (It is not pressed: it is looked at, that is all.)
        button = page.locator("button[type='submit']")

        check("the sign-in button exists", await button.count() >= 1)

        await browser.close()

    print("")

    if failures:
        print(f"❌ {failures} checks failed")

        return 1

    print("✅ The login fields get filled.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
