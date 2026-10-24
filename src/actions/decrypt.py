"""Decryption: from the AES-256-GCM envelope to the payload.

## Why the JSON is not read by the standard library `json`

In the Rust project the API sends certain decimals that `serde_json` reads
getting the last bit wrong (`145.01572080000003` landed in the **next**
double), and the cure was a local parser that entrusts **only the numbers**
to the standard library.

In Python the problem does not exist: `float()` rounds correctly, and gives
**the same double** as `str::parse::<f64>()` in Rust and as `Number()` in
JavaScript. So here the long decimals are right without having to write a
parser: it is read with `json.loads` and the two things that could diverge
from Rust are watched —

  · **integers stay integers** (the default `parse_int`), so counters do
    not become `1.0`;
  · a **non-finite** number is refused (`1e400` becomes `inf` in Python,
    while `str::parse::<f64>()` in Rust gives an error). The check is done
    with `parse_float`, that is, **while** the number is read: an `inf`
    never enters the tree.

`fingerprint` is a pure translation: 64-bit FNV-1a over the text
`iv|data|tag`, in lowercase hexadecimal with 16 digits. It is the identity
of a response, so it **must** give the same result as Rust: the two
projects must be able to read the same database.
"""

from __future__ import annotations

import base64
import json
import math
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# The key of the envelope. It lives here, as in Rust.
KEY_HEX = "9b7b4c85686a34cfa5af8070f3dddc4dfe5aa00fde441308852c600d628f7746"


class DecryptError(Exception):
    """The envelope does not open, or the payload is not readable."""


def _b64url(value: str) -> bytes:
    """base64url **with padding**, the way the API sends it."""
    try:
        return base64.urlsafe_b64decode(value)
    except Exception as error:  # noqa: BLE001 — the message matters more than the type
        raise DecryptError(f"invalid base64url: {error}") from error


def decrypt_response(data: str, iv: str, tag: str) -> Any:
    """Decrypts the envelope and reads the payload."""
    key = bytes.fromhex(KEY_HEX)

    if len(key) != 32:
        raise DecryptError("AES-256 key must contain 32 bytes")

    ciphertext = _b64url(data)
    iv_bytes = _b64url(iv)
    tag_bytes = _b64url(tag)

    if len(iv_bytes) != 12:
        raise DecryptError(f"Invalid GCM IV length: {len(iv_bytes)}")

    if len(tag_bytes) != 16:
        raise DecryptError(f"Invalid GCM tag length: {len(tag_bytes)}")

    try:
        plaintext = AESGCM(key).decrypt(iv_bytes, ciphertext + tag_bytes, None)
    except Exception as error:  # noqa: BLE001
        raise DecryptError(f"AES-GCM authentication/decryption failed: {error}") from error

    return parse_json(plaintext)


def _number(literal: str) -> float:
    """A decimal: `float()` rounds correctly.

    The check on the non-finite value is here, not afterwards: an `inf`
    must not even enter the tree.
    """
    value = float(literal)

    if not math.isfinite(value):
        raise DecryptError(f"non-finite number: {literal}")

    return value


def _reject_constant(name: str) -> Any:
    """`NaN` and `Infinity`: they are not JSON, and Rust refuses them."""
    raise DecryptError(f"non-finite number: {name}")


def parse_json(plaintext: bytes) -> Any:
    """Reads the decrypted JSON, with the decimals right."""
    try:
        text = plaintext.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DecryptError(f"the decrypted payload is not UTF-8: {error}") from error

    try:
        return json.loads(
            text,
            parse_float=_number,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise DecryptError(f"invalid JSON: {error}") from error


# ======================================================
# TEST: encrypting and decrypting must return the same payload
#
# It is not used by the program: it exists because a test that cannot
# encrypt cannot verify the decryption, and the test verifier needs it.
# ======================================================


def encrypt_for_test(payload: Any, nonce: bytes = b"\x07" * 12) -> tuple[str, str, str]:
    """Encrypts a payload the way the API does: `(data, iv, tag)` in base64url."""
    key = bytes.fromhex(KEY_HEX)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    sealed = AESGCM(key).encrypt(nonce, body, None)

    ciphertext, tag = sealed[:-16], sealed[-16:]

    encode = lambda raw: base64.urlsafe_b64encode(raw).decode("ascii")  # noqa: E731

    return encode(ciphertext), encode(nonce), encode(tag)


def decrypt_bytes(iv: str, data: str, tag: str) -> bytes:
    """The **plaintext** payload, without interpreting it.

    The verifier needs it, because it must be able to look at the exact
    bytes the API sent: if it went through the parser, the verifier would
    be checking the parser and not the data.
    """
    key = bytes.fromhex(KEY_HEX)

    ciphertext = _b64url(data)
    iv_bytes = _b64url(iv)
    tag_bytes = _b64url(tag)

    if len(iv_bytes) != 12:
        raise DecryptError(f"Invalid GCM IV length: {len(iv_bytes)}")

    if len(tag_bytes) != 16:
        raise DecryptError(f"Invalid GCM tag length: {len(tag_bytes)}")

    try:
        return AESGCM(key).decrypt(iv_bytes, ciphertext + tag_bytes, None)
    except Exception as error:  # noqa: BLE001
        raise DecryptError(f"AES-GCM authentication/decryption failed: {error}") from error
