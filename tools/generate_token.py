"""The "bot": mints a short-lived Bearer token for the data API.

    python tools/generate_token.py                 # 1 hour, the default
    python tools/generate_token.py --ttl 24h
    python tools/generate_token.py --ttl 7d
    python tools/generate_token.py --ttl 30m

Prints the raw token to stdout — nothing else — so it is easy to use
straight from a script:

    TOKEN=$(python tools/generate_token.py --ttl 2h)
    curl -H "Authorization: Bearer $TOKEN" https://your-vps/strikes/latest

Signs with `OD_JWT_SECRET` (the same secret `server/auth.py` verifies
against — see `server/config.py`), read from the environment or `.env`
in the project root. Needs no network access and no running server: a
valid token is just bytes signed with a secret both sides already have.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()

# The same two constants `server/auth.py` checks against: a token minted
# with a different algorithm or audience would be rejected, so these
# must not drift from that module.
ALGORITHM = "HS256"
AUDIENCE = "od-api"

_TTL_PATTERN = re.compile(r"^(\d+)([smhd])$")

_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
}


class TtlError(ValueError):
    """The `--ttl` value is not `<number><s|m|h|d>`."""


def parse_ttl(value: str) -> timedelta:
    match = _TTL_PATTERN.match(value.strip())

    if not match:
        raise TtlError(f"'{value}' is not a valid duration (expected e.g. 30m, 1h, 24h, 7d)")

    amount, unit = match.groups()

    return timedelta(**{_UNITS[unit]: int(amount)})


def _load_dotenv_secret(key: str) -> str | None:
    """A minimal `.env` reader: just `KEY=value` lines, no quoting rules.

    Good enough here — the project has no other use for a full `.env`
    parser, and this script must work even before `python-dotenv` (if
    ever added) is installed.
    """
    env_file = PROJECT_ROOT / ".env"

    if not env_file.exists():
        return None

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        name, _, value = line.partition("=")

        if name.strip() == key:
            return value.strip()

    return None


def load_secret() -> str:
    secret = os.environ.get("OD_JWT_SECRET") or _load_dotenv_secret("OD_JWT_SECRET")

    if not secret:
        print(
            "❌ OD_JWT_SECRET is not set (checked the environment and .env).\n"
            "   Set it to a long random string — the same one the server reads.",
            file=sys.stderr,
        )

        raise SystemExit(1)

    return secret


def make_token(secret: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)

    payload = {
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + ttl,
    }

    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])

    parser.add_argument(
        "--ttl",
        default="1h",
        help="how long the token stays valid: <number><s|m|h|d> (default: 1h)",
    )

    arguments = parser.parse_args(argv)

    try:
        ttl = parse_ttl(arguments.ttl)
    except TtlError as error:
        print(f"❌ {error}", file=sys.stderr)

        return 1

    secret = load_secret()

    token = make_token(secret, ttl)

    print(token)

    expires_at = datetime.now(timezone.utc) + ttl

    print(f"   (expires {expires_at.strftime('%Y-%m-%d %H:%M:%S')} UTC)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
