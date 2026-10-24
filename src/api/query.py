"""Reading the query params of the API URLs.

A single place for percent-decoding: the decryption, the computation of the
dimensions and the database parsers all use it.

`+` becomes a space and `%XX` a byte, the way `query.rs` does in the Rust
project: the two versions must read the same URLs in the same way.
"""

from __future__ import annotations


def percent_decode(value: str) -> str:
    """`%XX` → byte, `+` → space, the rest as it is."""
    raw = value.encode("utf-8", errors="surrogateescape")

    out = bytearray()
    index = 0

    while index < len(raw):
        # The condition is `index + 2 < len`, not `<=`: it is the one in
        # the Rust code, and a `%XX` right at the end would stay
        # undecoded.
        if raw[index] == ord("%") and index + 2 < len(raw):
            try:
                out.append(int(raw[index + 1 : index + 3].decode("ascii"), 16))
                index += 3
                continue
            except ValueError:
                pass

        out.append(ord(" ") if raw[index] == ord("+") else raw[index])

        index += 1

    return out.decode("utf-8", errors="replace")


def params(url: str) -> dict[str, str]:
    """Query string → decoded map."""
    result: dict[str, str] = {}

    if "?" not in url:
        return result

    _, query = url.split("?", 1)

    for pair in query.split("&"):
        if not pair:
            continue

        key, _, value = pair.partition("=")

        result[percent_decode(key)] = percent_decode(value)

    return result


def param(url: str, wanted: str) -> str | None:
    """A single parameter (empty string = absent)."""
    value = params(url).get(wanted)

    if value is None or value == "":
        return None

    return value


def first_param(url: str, keys: list[str]) -> str | None:
    """First parameter present among the ones given."""
    known = params(url)

    for key in keys:
        value = known.get(key)

        if value:
            return value

    return None


def params_json(url: str) -> str:
    """All the parameters as a JSON object."""
    import json

    return json.dumps(params(url), ensure_ascii=False)
