"""The Windows console speaks cp1252, not UTF-8.

The whole program writes emoji and accented characters: on Windows, without
this, the first `print("📥 ...")` blows the script up with
`UnicodeEncodeError`. It is called **before** printing anything.

In Python 3.7+ `reconfigure` already exists, so there is no need to reopen
the buffers by hand.
"""

from __future__ import annotations

import sys


def use_utf8_console() -> None:
    """Makes `stdout`/`stderr` capable of emoji and accents."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue

        reconfigure = getattr(stream, "reconfigure", None)

        if reconfigure is None:
            continue

        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A stream already closed or not reconfigurable is not a
            # reason to stop the program.
            pass
