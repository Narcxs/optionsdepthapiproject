"""The live session: from the captured record to the database, right away.

Until yesterday the live flow stopped at `data/network.jsonl` and the
row in the console: `load` filled the database by hand. Now the same
response makes the whole round trip while it arrives:

    response ──► data/network.jsonl   (the encrypted archive, the source)
                   │
                   ├──► data/decrypted.jsonl   (readable)
                   ├──► data/od.db         (the rows, right away)
                   └──► the row removed from network.jsonl

## Why the row is removed only afterwards

`network.jsonl` is the **source**: the database and the decrypted file are
derived. So the row is deleted only when the whole chain has
succeeded. If anything fails, the row **stays**: at the next
start it is retried, and the load is idempotent because the identity
of the response is the fingerprint of its body.

## One pipeline at a time

Several windows capture together, and each one wants to write to the same
file. A lock serializes the whole chain: no interleaved rows, and no
row deleted while another one is still reading it.

Translation of `src/actions/live.rs` and `src/actions/network.rs`.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import store as store_module
from actions import decode as decode_action
from actions.decode import DecodeError
from config import Config, matches
from api import endpoints

# The browser went away: every call on it raises this. It is not a bug in the
# program — it is the user closing the window, or Chromium falling over — and
# it deserves one clear line instead of a traceback from inside Playwright.
# Imported at the top so the check works even outside `run()`.
try:
    from playwright._impl._errors import TargetClosedError
except Exception:  # noqa: BLE001 — a different Playwright version
    class TargetClosedError(Exception):  # type: ignore[no-redef]
        """Placeholder: if Playwright is not there, nothing can raise it."""


class BrowserGone(Exception):
    """The browser is closed: no window can be opened, ever again.

    Raised where the difference matters — a slow page and a dead browser
    look alike at the call site, and treating the second like the first is
    how a session ends up opening windows onto nothing.
    """


def browser_is_gone(error: BaseException) -> bool:
    """Is this the browser being closed, rather than a real failure?

    Playwright answers a closed target with `TargetClosedError`, but the
    message travels through several layers and a few paths raise a plain
    `Error` instead, so the text is checked too. "Browser has been closed"
    is written by Playwright itself, not by us.
    """
    if isinstance(error, (TargetClosedError, BrowserGone)):
        return True

    text = str(error).lower()

    return "has been closed" in text or "target closed" in text or "browser closed" in text


CAPTURE_FILE = "data/network.jsonl"
DECRYPTED_FILE = "data/decrypted.jsonl"

# Touching this file from another process asks the session to shut down
# cleanly — the same path as Ctrl+C, so `supervisor.py` can stop it without
# killing the process (which would skip closing the browser and writing
# `active: false` into config.json).
STOP_FLAG = Path("data/.stop")

# How many responses were captured and how many did not decrypt.
_captured = 0
_decode_errors = 0

# One pipeline at a time: the capture file is only one.
_pipeline_lock = threading.Lock()


def captured() -> int:
    return _captured


def decode_errors() -> int:
    return _decode_errors


def left_in_capture(capture: str = CAPTURE_FILE) -> int:
    """How many rows are left in the capture at the end of the session.

    There should be none: every response that succeeded leaves the
    file as soon as it is written to the database.
    """
    path = Path(capture)

    if not path.exists():
        return 0

    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def append_capture(record: dict[str, Any], capture: str = CAPTURE_FILE) -> str:
    """Writes the row into the capture file and returns it.

    A single `write` per record: two windows cannot interleave in the
    middle of a row.
    """
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"

    path = Path(capture)

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()

    return line.strip()


def remove_line(raw: str, capture: str = CAPTURE_FILE) -> None:
    """Removes from the capture **one** row, the exact one.

    Not "all the copies": two identical responses are two different records
    in the file, and treating them as one would make one of them
    disappear by mistake.

    The file is rewritten — it is small on purpose — and replaced with
    a `replace`, so an interruption halfway does not leave a truncated capture.
    """
    path = Path(capture)

    if not path.exists():
        return

    wanted = raw.strip()

    kept: list[str] = []
    removed = False

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        if not removed and line.strip() == wanted:
            removed = True

            continue

        kept.append(line)

    if not removed:
        return

    temporary = path.with_suffix(path.suffix + ".tmp")

    temporary.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

    temporary.replace(path)


class Live:
    """The live session: the database and the files."""

    def __init__(self, database: str, capture: str = CAPTURE_FILE, decrypted: str = DECRYPTED_FILE):
        self.capture = capture
        self.decrypted = decrypted

        self.connection = store_module.open_database(database)

        print(f"🗄️ Database ready: {database}")

    def handle(self, raw: str) -> None:
        """The full round trip of a response that has just arrived."""
        global _decode_errors

        with _pipeline_lock:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as error:
                print(f"⚠️ record cannot be parsed ({error}): it stays in the capture")

                return

            try:
                decoded = decode_action.decode(record)
            except DecodeError as error:
                # This is not a failure: it is a response that is not needed
                # (POST, missing envelope…). It stays in the capture, and `load`
                # will skip it the same way.
                _decode_errors += 1

                print(f"⚠️ decryption failed: {error}")

                return

            print(decoded.line())

            # The readable file is written **before** touching the capture,
            # so a decrypted response is not lost even if the
            # database is not there.
            try:
                decode_action.append_decoded(self.decrypted, decoded)
            except OSError as error:
                print(f"❌ {self.decrypted} not writable ({error}): the row stays in the capture")

                return

            # The database: the same path as the bulk load.
            try:
                stored = store_module.store_record(self.connection, decoded.record())
            except store_module.StoreError as error:
                print(
                    f"⚠️ not written to the database ({error}): the row stays in the capture "
                    "and will be loaded later"
                )

                return

            if isinstance(stored, store_module.New):
                # The count is the rows of **this** response, not the size of
                # the database: seven series of the same dashboard arrive
                # together and each writes its own. Written as "rows in the
                # database" it read like a total that never grew, which made
                # it look as if nothing were being added.
                print(f"🗄️ {stored.rows} rows written (this response)")

            # The whole chain succeeded: the row is no longer needed.
            try:
                remove_line(raw, self.capture)
            except OSError as error:
                print(f"⚠️ row not removed from {self.capture}: {error}")


# ======================================================
# THE CAPTURE
# ======================================================


def accepts(targets: list[str], backend_host: str, url: str) -> bool:
    """Is this response one of those to capture?"""
    return matches(targets, backend_host, url) and endpoints.detect(url).is_needed()


async def attach(page: Any, window_name: str, targets: list[str], backend_host: str, live: Live):
    """Listens to the responses of one window and sends them to the pipeline.

    The listener is attached **before** the page navigates: this way the
    response that arrives during the load is not lost.
    """

    def on_response(response: Any) -> None:
        global _captured

        try:
            url = response.url
        except Exception:  # noqa: BLE001 — a vanished response is not a failure
            return

        if not accepts(targets, backend_host, url):
            return

        # The response must be read now: the browser may release the body.
        async def consume() -> None:
            global _captured

            try:
                body = await response.json()
            except Exception as error:  # noqa: BLE001
                print(f"⚠️ [{window_name}] body not readable ({error})")

                return

            try:
                request = response.request
                method = request.method
            except Exception:  # noqa: BLE001
                method = "GET"

            record = {
                "timestamp": int(__import__("time").time()),
                "window": window_name,
                "method": method,
                "url": url,
                "status": response.status,
                "body": body,
            }

            try:
                raw = append_capture(record, live.capture)
            except OSError as error:
                print(f"❌ [{window_name}] I cannot write the capture ({error})")

                return

            _captured += 1

            # The pipeline is synchronous and touches the disk: outside the loop.
            #
            # The error is caught here: a task that nobody awaits, if it
            # raises, prints "Task exception was never retrieved" and nobody
            # knows which response it comes from.
            try:
                await asyncio.to_thread(live.handle, raw)
            except Exception as error:  # noqa: BLE001
                print(f"❌ [{window_name}] the response was not archived ({error})")
                print("   the row stays in the capture and will be retried with `load`")

        asyncio.create_task(consume())

    page.on("response", on_response)


async def new_tracked_window(
    browser: Any,
    url: str,
    window_name: str,
    targets: list[str],
    backend_host: str,
    live: Live,
    storage: dict | None = None,
):
    """A **window** that listens, already positioned on the URL.

    One window per context: `context.new_page()` alone opens a **tab**
    in the window of the context, so the pages would all end up as tabs
    in a single window. With one context per page, Chromium opens a real
    window for each one — the same thing the Rust project does with
    `new_window: true`.

    **But a new context is not authenticated.** Contexts do not share the
    cookies, and this app does not keep the session in a cookie: it keeps it in
    the `localStorage` (`accessToken`, `refreshToken`). So to every new window
    the `storage_state` taken from the login context is passed — cookies
    **and** localStorage in a single shot. Without it, the new window asks to
    sign in again.
    """
    context = await browser.new_context(storage_state=storage) if storage else await browser.new_context()

    page = await context.new_page()

    await attach(page, window_name, targets, backend_host, live)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as error:  # noqa: BLE001
        # Two very different things end up here, and they must not be
        # treated the same:
        #
        #   · the page is slow → the window still exists, the responses
        #     will arrive, and carrying on is right;
        #   · the browser is gone → every call after this one fails with
        #     the same message, so carrying on produces four identical
        #     failures and a session that only looks alive. Measured: the
        #     window looked "created", then the dashboard could not be
        #     opened, and the program went on opening windows onto nothing.
        if browser_is_gone(error):
            raise BrowserGone(f"[{window_name}] the browser has been closed") from error

        print(f"⚠️ [{window_name}] the page did not finish loading: {error}")

    return page


# ======================================================
# THE BUTTON THAT MOVES TIME FORWARD
# ======================================================

SKIP_FORWARD = "button._skipForwardButton_184mf_81"


async def wait_for_text(page: Any, text: str, timeout_seconds: float = 30) -> bool:
    """Waits for a text to appear on the page."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds

    while asyncio.get_running_loop().time() < deadline:
        try:
            found = await page.evaluate(
                "text => document.body.innerText.includes(text)",
                text,
            )
        except Exception:  # noqa: BLE001
            found = False

        if found:
            return True

        await asyncio.sleep(0.1)

    return False


async def click_skip_forward(page: Any, timeout_seconds: float = 30) -> bool:
    """Presses the button that moves time forward.

    The button shows up with the graphics **dimmed** (`bg-inputDisabled`)
    but without the `disabled` attribute: it is a toggle, and the state it
    starts from is the off one. So the color classes are not inspected —
    whoever reads them concludes that it is disabled and waits forever.

    It waits for it to be there and to receive clicks, then presses it with the
    browser's **real** click (coordinates + mouse event), because a
    synthetic `element.click()` on a React/Radix button may never
    reach the handler.

    The element is looked up **inside** the wait, not just once: these
    pages draw the buttons when they have finished loading.
    """
    print("⏭️ Waiting for the Skip Forward button...")

    deadline = asyncio.get_running_loop().time() + timeout_seconds

    while asyncio.get_running_loop().time() < deadline:
        ready = await _button_is_ready(page)

        if ready:
            try:
                await page.click(SKIP_FORWARD, timeout=5_000)
            except Exception as error:  # noqa: BLE001
                # The script says it is there but it will not let itself be
                # caught: it is pressed from here, not a reason to stop.
                print(f"   (the button will not let itself be caught: {error} — pressing it from here)")

                try:
                    await page.evaluate(
                        "selector => document.querySelector(selector).click()",
                        SKIP_FORWARD,
                    )
                except Exception as inner:  # noqa: BLE001
                    print(f"   ⚠️ I could not press it: {inner}")

                    return False

            print("⏭️ Skip Forward clicked.")

            await asyncio.sleep(0.3)

            if await _button_still_there(page):
                print("⏭️ The dashboard has moved on.")
            else:
                print("⏭️ The button is no longer there: the dashboard has moved on.")

            return True

        await asyncio.sleep(0.1)

    print(f"❌ Skip Forward button not found (or never ready) after {timeout_seconds} seconds.")

    await describe_buttons(page)

    return False


async def _button_is_ready(page: Any) -> bool:
    """Is the button there and is it the moment to press it?"""
    try:
        return bool(
            await page.evaluate(
                """
                selector => {
                    const button = document.querySelector(selector);

                    if (!button) {
                        return false;
                    }

                    // A real `disabled`, or an `aria-disabled`, stops the click.
                    if (button.disabled === true || button.getAttribute("aria-disabled") === "true") {
                        return false;
                    }

                    // If it does not receive clicks, it is not the moment.
                    if (getComputedStyle(button).pointerEvents === "none") {
                        return false;
                    }

                    // `data-state` is the state the app updates: "closed"
                    // is the one it can start from.
                    const state = button.getAttribute("data-state");

                    if (state !== null && state !== "closed") {
                        return false;
                    }

                    // It must have a position: a real click goes to coordinates.
                    const rect = button.getBoundingClientRect();

                    return rect.width > 0 && rect.height > 0;
                }
                """,
                SKIP_FORWARD,
            )
        )
    except Exception:  # noqa: BLE001
        return False


async def _button_still_there(page: Any) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                selector => {
                    const button = document.querySelector(selector);

                    if (!button) {
                        return false;
                    }

                    return !(button.disabled === true
                             || button.getAttribute("aria-disabled") === "true");
                }
                """,
                SKIP_FORWARD,
            )
        )
    except Exception:  # noqa: BLE001
        return False


async def describe_buttons(page: Any) -> None:
    """The buttons present on the page, to understand what is there.

    The app generates the classes: when one changes, the selector stops
    working and there is no way to guess it. Here the page is asked which
    buttons it has, so the right one can be seen right away.
    """
    try:
        buttons = await page.evaluate(
            """
            () => [...document.querySelectorAll("button")]
                .filter((button) => {
                    const rect = button.getBoundingClientRect();

                    return rect.width > 0 && rect.height > 0;
                })
                .slice(0, 40)
                .map((button) => {
                    const style = getComputedStyle(button);

                    return {
                        text: (button.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 40),
                        classes: button.className.split(/\\s+/)
                            .filter((name) => name.startsWith("_") || name.includes("Button"))
                            .join(" "),
                        state: button.getAttribute("data-state") ?? "",
                        disabled: button.disabled === true,
                        pointerEvents: style.pointerEvents,
                    };
                })
            """
        )
    except Exception as error:  # noqa: BLE001
        print(f"   (I could not read the buttons: {error})")

        return

    print(f"   🔎 The {len(buttons)} visible buttons of the page:")

    for index, button in enumerate(buttons):
        print(
            f"   {index:>2}. \"{button['text']}\" [{button['classes']}] "
            f"data-state=\"{button['state']}\" disabled={button['disabled']} "
            f"pointer-events={button['pointerEvents']}"
        )


# ======================================================
# LOGIN AND DASHBOARD
# ======================================================


async def fill_when_ready(
    page: Any,
    selector: str,
    value: str,
    timeout_seconds: float = 60,
) -> bool:
    """Writes into a field while waiting for the page to stop redrawing it.

    The login page is a React app that re-renders while it loads: the
    email field is **detached from the DOM** and recreated, and a `fill` at
    that moment fails with "element was detached from the DOM, retrying" —
    Playwright retries on its own, but only until its timeout expires.

    Here it is retried by hand, calmly: it waits for the field to be there,
    it tries to write, and if the page has redrawn it, it retries. At the
    end it checks that the value is **really** inside, because `fill` may
    succeed on an element that is then replaced.
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds

    attempts = 0

    while asyncio.get_running_loop().time() < deadline:
        attempts += 1

        try:
            field = page.locator(selector).first

            await field.wait_for(state="visible", timeout=5_000)

            await field.fill(value, timeout=5_000)

            # Did the value stay? If the page has redrawn the field after
            # the write, it is as if it had never been written.
            if await field.input_value(timeout=2_000) == value:
                return True
        except Exception:  # noqa: BLE001 — it retries, it does not give up
            pass

        await asyncio.sleep(0.3)

    print(f"   ⚠️ I could not write into {selector} ({attempts} attempts)")

    return False


async def login(page: Any, email: str, password: str) -> bool:
    """Enters the app (or notices if it has already entered).

    Returns `True` if the login worked, `False` if not: a login that does
    not succeed must not make the session fall, which may still be useful
    (the profile on disk may already be authenticated).
    """
    print("🔐 Starting login...")

    current_url = page.url

    print(f"🔎 Current URL: {current_url}")

    if "/home" in current_url:
        print("✅ Already logged in!")

        return True

    # The fields do not exist at `domcontentloaded`: the page is React and
    # it draws them later. It waits for them to appear, instead of trying and failing.
    print("📧 Looking for email field...")

    try:
        await page.locator("input[name='email']").first.wait_for(
            state="visible", timeout=30_000
        )
    except Exception:  # noqa: BLE001 — it may be a different page: it tries anyway
        print("   (the email field did not appear: it tries anyway)")

    if not await fill_when_ready(page, "input[name='email']", email):
        return False

    print("📧 Email inserted.")

    if not await fill_when_ready(page, "input[name='password']", password):
        return False

    print("🔐 Password inserted.")

    button = page.locator("button[type='submit']").first

    try:
        await button.wait_for(state="visible", timeout=10_000)

        if await button.is_disabled():
            print("⚠️ Login button is still disabled.")

            return False

        print("🚀 Clicking Log In...")

        await button.click(timeout=10_000)
    except Exception as error:  # noqa: BLE001
        print(f"⚠️ I could not press Log In: {error}")

        return False

    print("✅ Login clicked.")

    # Did the login work? It looks at where we ended up, we do not say it.
    deadline = asyncio.get_running_loop().time() + 30

    while asyncio.get_running_loop().time() < deadline:
        if "/sign-in" not in page.url:
            print(f"✅ We are in: {page.url}")

            return True

        await asyncio.sleep(0.5)

    print("⚠️ The login did not lead out of the sign-in page.")

    return False


async def open_dashboard(page: Any, dashboard_name: str) -> bool:
    """Opens a dashboard by name and moves time forward."""
    print(f"📊 Opening dashboard: {dashboard_name}")

    if not await wait_for_text(page, "My Dashboard", 30):
        print("❌ 'My Dashboard' did not appear")

        return False

    print("🖱️ Clicking My Dashboard...")

    await page.evaluate(
        """
        () => {
            const links = [...document.querySelectorAll("a.menuLink")];

            const dashboard = links.find(
                el => el.textContent?.trim().includes("My Dashboard")
            );

            if (!dashboard) {
                throw new Error("My Dashboard not found");
            }

            dashboard.click();

            return true;
        }
        """
    )

    print("📊 Waiting for dashboard popup...")

    if not await wait_for_text(page, "Dashboards", 30):
        print("❌ The dashboard popup did not open")

        return False

    print("📊 Dashboard popup is open.")

    print(f"🔎 Waiting for dashboard: {dashboard_name}")

    deadline = asyncio.get_running_loop().time() + 30

    found = False

    while asyncio.get_running_loop().time() < deadline:
        found = bool(
            await page.evaluate(
                """
                name => [...document.querySelectorAll("h3")]
                    .some(el => el.textContent?.trim() === name)
                """,
                dashboard_name,
            )
        )

        if found:
            break

        await asyncio.sleep(0.1)

    if not found:
        print(f"❌ Dashboard '{dashboard_name}' not found")

        return False

    print(f"✅ Dashboard found: {dashboard_name}")

    await page.evaluate(
        """
        name => {
            const titles = [...document.querySelectorAll("h3")];

            const title = titles.find(el => el.textContent?.trim() === name);

            if (!title) {
                throw new Error("Dashboard not found");
            }

            title.click();

            return true;
        }
        """,
        dashboard_name,
    )

    print("🚀 Dashboard clicked!")

    await asyncio.sleep(2)

    await click_skip_forward(page, 30)

    return True


# ======================================================
# THE SESSION
# ======================================================


async def run(config: Config) -> int:
    """Opens the browser, the windows, and stays listening until Ctrl+C."""
    from playwright.async_api import async_playwright

    backend_host = config.backend_host()

    print(f"🗄️ Database: {store_module.DEFAULT_DATABASE}")

    live = Live(store_module.DEFAULT_DATABASE)

    profile = Path("browser_profiles/python")

    profile.mkdir(parents=True, exist_ok=True)

    print(f"📁 Chromium profile: {profile.resolve()}")

    async with async_playwright() as playwright:
        # A **persistent context** for the login: it keeps the profile on disk,
        # so on the following times the login is not done again. It is the only
        # way to have `user_data_dir` — `launch()` does not accept it.
        #
        # Then **one window per context**: Playwright opens a tab when a
        # page is requested from the same context, and a window when the
        # context is new. The persistent context is the first window (the
        # login one); the others are created from `browser.new_context()`.
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile.resolve()),
            headless=False,
            args=["--window-size=1400,900"],
        )

        browser = context.browser

        print("✅ Chromium started.")

        pages = context.pages

        page = pages[0] if pages else await context.new_page()

        print("========================================")
        print("🔐 LOGIN")
        print("========================================")

        await page.goto(config.url_od, wait_until="domcontentloaded", timeout=60_000)

        # It waits for the app to finish drawing itself: the page is React and
        # it re-renders while it loads, detaching the fields from the DOM.
        try:
            await page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:  # noqa: BLE001 — it may never get there: it goes on
            pass

        if await login(page, config.email_od, config.password_od):
            print("🔐 Login session ready.")
        else:
            # It does not give up: the profile on disk may already be
            # authenticated, and the windows open anyway. If the login was really
            # needed, it will show — the pages will not load the data and no
            # response will arrive in the console.
            print("⚠️ Login failed: it goes on anyway.")
            print("   If the pages stay empty, the login is the reason.")

        # The session, for the new windows.
        #
        # The app keeps the authentication in the `localStorage` (`accessToken`,
        # `refreshToken`), not in a cookie: a new context, on its own, is a
        # stranger and asks to sign in. `storage_state()` takes cookies **and**
        # localStorage, and it is passed to every new window.
        #
        # It is taken **every time**, not just once: the app renews the
        # token while it navigates, and an old copy is a copy that expires.
        state = await context.storage_state()

        origins = state.get("origins") or []

        keys = sum(len(origin.get("localStorage") or []) for origin in origins)

        print(
            f"🔑 Session ready: {len(state.get('cookies') or [])} cookies · "
            f"{keys} localStorage entries"
        )

        # The login window listens to the first dashboard (as in Rust).
        first_dashboard = next(iter(config.dashboard.items()), None)

        if first_dashboard is not None:
            key, dashboard = first_dashboard

            await attach(page, key, dashboard.targets, backend_host, live)

            print("========================================")
            print(f"📊 Opening dashboard: {key} ({dashboard.name})")
            print("========================================")

            print(f"🌐 [{key}] listening on {len(dashboard.targets)} targets")

            if await open_dashboard(page, dashboard.name):
                config.set_active("dashboard", key, True)

                print(f"✅ Dashboard '{dashboard.name}' ready · active: true")
            else:
                config.set_active("dashboard", key, False)

        # The other dashboards: a window of their own.
        #
        # `windows_gone`: if the browser has been closed, every call on it
        # raises, and going through the remaining dashboards only produces
        # four identical failures. Once is enough, then it stops and says so.
        windows_gone = False

        for key, dashboard in list(config.dashboard.items())[1:]:
            if windows_gone:
                config.set_active("dashboard", key, False)

                continue

            print("")
            print("========================================")
            print(f"📊 Opening dashboard: {key} ({dashboard.name})")
            print("========================================")

            config.set_active("dashboard", key, False)

            print(f"🌐 [{key}] listening on {len(dashboard.targets)} targets")

            try:
                window = await new_tracked_window(
                    browser,
                    "https://app.optionsdepth.com/home",
                    key,
                    dashboard.targets,
                    backend_host,
                    live,
                    # Fresh, not a copy from half an hour ago: the token is renewed.
                    await context.storage_state(),
                )
            except Exception as error:  # noqa: BLE001
                if browser_is_gone(error):
                    print(f"⚠️ [{key}] the browser is closed: no more windows.")
                    print("   Everything captured so far is already in the database.")

                    windows_gone = True

                    continue

                print(f"⚠️ [{key}] the window could not be opened: {error}")

                continue

            print("🪟 New window created.")

            await asyncio.sleep(0.8)

            try:
                if await open_dashboard(window, dashboard.name):
                    config.set_active("dashboard", key, True)

                    print(f"✅ Dashboard '{dashboard.name}' ready · active: true")
                else:
                    config.set_active("dashboard", key, False)
            except Exception as error:  # noqa: BLE001
                if browser_is_gone(error):
                    print(f"⚠️ [{key}] the browser is closed: no more windows.")

                    windows_gone = True

                    continue

                print(f"⚠️ [{key}] the dashboard could not be opened: {error}")

        # The pages: opened by URL, and the button only if they declare it.
        for key, page_config in config.pages.items():
            if windows_gone:
                config.set_active("pages", key, False)

                continue

            print("")
            print("========================================")
            print(f"🌐 Opening page: {key} ({page_config.url})")
            print("========================================")

            config.set_active("pages", key, False)

            print(f"🌐 [{key}] listening on {len(page_config.targets)} targets")

            try:
                window = await new_tracked_window(
                    browser,
                    page_config.url,
                    key,
                    page_config.targets,
                    backend_host,
                    live,
                    # Fresh, not a copy from half an hour ago: the token is renewed.
                    await context.storage_state(),
                )
            except Exception as error:  # noqa: BLE001
                if browser_is_gone(error):
                    print(f"⚠️ [{key}] the browser is closed: no more windows.")

                    windows_gone = True

                    continue

                print(f"⚠️ [{key}] the page could not be opened: {error}")

                continue

            print("🪟 New window created.")

            await asyncio.sleep(0.8)

            try:
                # The wait is longer than the dashboards' one: these pages load
                # several datasets and draw a grid.
                if page_config.next and not await click_skip_forward(window, 60):
                    print(f"⚠️ Page '{key}': time was not moved forward")
            except Exception as error:  # noqa: BLE001
                if browser_is_gone(error):
                    print(f"⚠️ [{key}] the browser is closed: no more windows.")

                    windows_gone = True

                    continue

                print(f"⚠️ [{key}] the time was not moved forward: {error}")

            config.set_active("pages", key, True)

            print(f"✅ Page '{key}' ready · active: true · {window.url}")

        print("")
        print("========================================")
        print("🚀 PYOD IS RUNNING")
        print("========================================")

        print(f"📊 Dashboards: {len(config.dashboard)}")
        print(f"🌐 Pages: {len(config.pages)}")
        print("")
        print("🧹 The row leaves network.jsonl as soon as it is decrypted and written:")
        print("   the file stays light. If anything fails, the row stays there")
        print("   and it is retried (the load is idempotent).")
        print("")
        print("Press Ctrl+C to exit (or create data/.stop to stop it from another process).")

        try:
            while True:
                if STOP_FLAG.exists():
                    print("")
                    print(f"🛑 Stop signal received ({STOP_FLAG}).")

                    STOP_FLAG.unlink(missing_ok=True)

                    break

                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass

        print("")
        print("🛑 Shutting down...")

        # `context.browser` may not be there with a persistent context: in
        # that case the context is closed, which takes the browser away too.
        #
        # Closing can fail, and that is not a problem worth a traceback: if
        # the browser is already gone, it is already closed. What must not
        # happen is that this exception skips everything below it — the
        # count of what was captured, what is left in the capture, and
        # `active: false` in `config.json`. Leaving the configuration saying
        # `active: true` for windows that no longer exist is how a restart
        # ends up confused.
        try:
            if browser is not None:
                await browser.close()
            else:
                await context.close()
        except Exception as error:  # noqa: BLE001
            print(f"⚠️ the browser did not close cleanly ({type(error).__name__}): it is already gone.")

    print(f"✅ Session closed: {captured()} responses captured and archived live.")

    if decode_errors() > 0:
        print(f"⚠️ {decode_errors()} responses that cannot be decrypted (see the warnings above).")

    left = left_in_capture(CAPTURE_FILE)

    if left > 0:
        print(
            f"⚠️ {left} rows are left in {CAPTURE_FILE} (not decrypted or not written): "
            "they are retried at the next start, or with `load`."
        )

    for key, _ in config.previous_state():
        section = "dashboard" if key in config.dashboard else "pages"

        config.set_active(section, key, False)

    print("📋 config.json updated: active false for all windows.")

    return 0


def run_session() -> int:
    """The entry point of the "capture" command."""
    try:
        config = Config.load()
    except Exception as error:  # noqa: BLE001
        print(f"❌ {error}")

        return 1

    print("🚀 Starting PyOD...")
    print(f"🌐 Target: {config.url_od}")
    print(f"📧 Email: {config.email_od}")

    try:
        return asyncio.run(run(config))
    except KeyboardInterrupt:
        print("")
        print("🛑 Interrupted.")

        return 0
    except Exception as error:  # noqa: BLE001
        # The last net. If the browser is closed while the session is
        # running — the window closed by hand, Chromium falling over, the
        # machine going to sleep — Playwright raises from deep inside its
        # own stack, and what the user saw was a traceback that scrolled the
        # console away, with no line saying what had happened. Measured by
        # closing Chromium during a session:
        #
        #   playwright._impl._errors.TargetClosedError:
        #   BrowserContext.storage_state: Target page, context or browser
        #   has been closed
        #
        # This says it in one line, and says the thing that matters: what
        # was captured is already written.
        if browser_is_gone(error):
            print("")
            print("🛑 The browser has been closed: the session ends here.")
            print(f"✅ What was captured is already in the database ({captured()} responses).")
            print("   Start it again with `python main.py`.")

            return 0

        print("")
        print(f"❌ Unexpected failure: {type(error).__name__}: {error}")

        import traceback

        print("")
        print("   The full trace, for whoever needs to look:")
        traceback.print_exc()

        return 1
