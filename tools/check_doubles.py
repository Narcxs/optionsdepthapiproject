"""Are the doubles Python writes the same ones Rust writes?

The Rust project existed because of a `serde_json` bug on the last bit of
certain decimals. Here we compare the text: for every response present in
both decrypted files, we look at the **bits** of the doubles, not the
printed digits — two different spellings can represent the same double,
but a different bit is a different value.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from console import use_utf8_console  # noqa: E402

use_utf8_console()


def doubles(value, path: str = "$") -> dict[str, float]:
    """All the doubles of a payload, indexed by their path."""
    found: dict[str, float] = {}

    if isinstance(value, bool):
        return found

    if isinstance(value, float):
        found[path] = value

        return found

    if isinstance(value, dict):
        for key, item in value.items():
            found.update(doubles(item, f"{path}.{key}"))

    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(doubles(item, f"{path}[{index}]"))

    return found


def bits(value: float) -> str:
    return f"0x{struct.unpack('<Q', struct.pack('<d', value))[0]:016x}"


def load(path: Path) -> dict[str, dict]:
    rows = {}

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        record = json.loads(line)

        rows[record["fingerprint"]] = record

    return rows


def main(argv: list[str]) -> int:
    ours = load(Path(argv[1] if len(argv) > 1 else "data/decrypted.jsonl"))
    theirs = load(Path(argv[2] if len(argv) > 2 else "../rust_od/data/decrypted.jsonl"))

    common = sorted(set(ours) & set(theirs))

    print(f"Python records: {len(ours)}")
    print(f"Rust records:   {len(theirs)}")
    print(f"shared:         {len(common)}")
    print("")

    differing = 0
    compared = 0
    examples = []

    for identifier in common:
        a = doubles(ours[identifier]["payload"])
        b = doubles(theirs[identifier]["payload"])

        keys = set(a) | set(b)

        for key in keys:
            compared += 1

            if key not in a or key not in b or bits(a[key]) != bits(b[key]):
                differing += 1

                if len(examples) < 5:
                    examples.append(
                        (
                            ours[identifier]["endpoint"],
                            key,
                            a.get(key),
                            b.get(key),
                            bits(a[key]) if key in a else "-",
                            bits(b[key]) if key in b else "-",
                        )
                    )

    print(f"values compared: {compared}")
    print(f"differing bits:  {differing}")
    print("")

    for endpoint, key, mine, other, my_bits, other_bits in examples:
        print(f"   ❌ {endpoint} {key}")
        print(f"      Python: {mine} {my_bits}")
        print(f"      Rust:   {other} {other_bits}")

    print("")

    # Zero shared records is not the same thing as "the values differ": it
    # means nothing was measured, and a green or a red here would both be
    # meaningless. It happens when one of the two files has been decrypted
    # again while the other has not — measured: Python 208 records against
    # Rust's 73, no fingerprint in common. Say so instead of reporting a
    # difference that was never looked at.
    if compared == 0:
        print("❌ nothing could be compared: the two files have no response in common.")
        print(
            f"   Python: {len(ours)} records · Rust: {len(theirs)} · shared: {len(common)}"
        )
        print("   The Rust file is behind: decrypt the same capture into it, or compare")
        print("   `../rust_od/data/decrypted.jsonl` with a copy of this one.")

        return 1

    ok = differing == 0

    print(
        "✅ the same doubles, bit for bit." if ok else "❌ the two files have different values."
    )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
