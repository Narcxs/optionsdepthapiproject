"""Shows every token ever issued, and whether it still works right now.

    python tools/list_tokens.py

Status is computed here, not stored: "expired" compares `expires_at`
against the current time, "revoked" is set by `tools/revoke_token.py`,
"active" is neither. `server/auth.py` re-derives the same thing (plus
the signature check a listing like this cannot do) on every request —
this is a read-only view for a human, not what the server trusts.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

from server.config import settings  # noqa: E402
from server.tokens_store import list_tokens, open_tokens_db  # noqa: E402


def status_of(expires_at: str, revoked_at: str | None) -> str:
    if revoked_at:
        return "revoked"

    expires = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    if expires < datetime.now(timezone.utc):
        return "expired"

    return "active"


def main() -> int:
    connection = open_tokens_db(settings.tokens_database)

    rows = list_tokens(connection)

    connection.close()

    if not rows:
        print("No token has been issued yet (see tools/generate_token.py).")

        return 0

    print(f"{'jti':<32}  {'name':<16}  {'status':<8}  {'issued_at':<19}  {'expires_at':<19}")

    for row in rows:
        status = status_of(row["expires_at"], row["revoked_at"])

        print(
            f"{row['jti']:<32}  {row['name']:<16}  {status:<8}  "
            f"{row['issued_at']:<19}  {row['expires_at']:<19}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
