"""Cuts off one token — or every live token for one name — right away.

    python tools/revoke_token.py alice          # every live token named "alice"
    python tools/revoke_token.py a1b2c3...      # one specific token, by its jti

Takes effect on the very next request: `server/auth.py` checks
`tokens.db` on every call, there is nothing to restart. Does not touch
`OD_JWT_SECRET` or any other token — that is the whole point of having
individual ids (see `server/tokens_store.py`).

A jti is 32 hex characters (`tools/generate_token.py` prints it when it
mints a token); anything else is treated as a name.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from server.config import settings  # noqa: E402
from server.tokens_store import open_tokens_db, revoke_by_jti, revoke_by_name  # noqa: E402

_JTI_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python tools/revoke_token.py <name-or-jti>", file=sys.stderr)

        return 1

    target = argv[0]

    connection = open_tokens_db(settings.tokens_database)

    if _JTI_PATTERN.match(target):
        revoked = revoke_by_jti(connection, target)

        connection.close()

        if revoked:
            print(f"✅ revoked token {target}")

            return 0

        print(f"❌ {target}: no live token with this id (already revoked, expired, or never issued)")

        return 1

    count = revoke_by_name(connection, target)

    connection.close()

    if count == 0:
        print(f"❌ no live token named '{target}' (check tools/list_tokens.py)")

        return 1

    plural = "s" if count != 1 else ""

    print(f"✅ revoked {count} token{plural} named '{target}'")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
