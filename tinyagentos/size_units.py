"""Single source of truth for parsing byte-size strings.

taOS reads size strings from several unrelated producers -- incus
(``limits.memory``, ``incus info``, ``incus image list``), docker
(``--memory 512m``), btrfs (``filesystem show``) and truncate(1)
(``taos worker resize-storage 500G``) -- and each spells sizes a little
differently. Five separate parsers used to exist; two of them returned 0
or raised on values taOS itself writes.

Suffix conventions follow the producers:

* IEC suffixes (``KiB`` .. ``PiB``) are 1024-based.
* SI suffixes (``kB`` .. ``PB``) are 1000-based, which is how incus and
  btrfs document them -- ``limits.memory: 2GB`` really is 2,000,000,000
  bytes, not 2 GiB.
* A bare unit letter (``512m``, ``100G``) is 1024-based: the
  ``docker --memory`` / ``truncate(1)`` convention.
* A bare number is a byte count.

Parsing is case-insensitive, because incus accepts ``512m``, ``512MiB``
and ``512MIB`` interchangeably.
"""
from __future__ import annotations

_UNITS: dict[str, int] = {
    "B": 1,
    # IEC -- 1024-based.
    "KIB": 1024,
    "MIB": 1024 ** 2,
    "GIB": 1024 ** 3,
    "TIB": 1024 ** 4,
    "PIB": 1024 ** 5,
    # SI -- 1000-based.
    "KB": 1000,
    "MB": 1000 ** 2,
    "GB": 1000 ** 3,
    "TB": 1000 ** 4,
    "PB": 1000 ** 5,
    # Bare unit letters -- docker / truncate(1), 1024-based.
    "K": 1024,
    "M": 1024 ** 2,
    "G": 1024 ** 3,
    "T": 1024 ** 4,
    "P": 1024 ** 5,
}

# Longest suffix first so "MIB" wins over the "B" it ends with, and "GB"
# over "B".
_SUFFIXES: tuple[str, ...] = tuple(sorted(_UNITS, key=len, reverse=True))

BYTES_PER_MIB = 1024 ** 2
BYTES_PER_GIB = 1024 ** 3


def parse_size_bytes(value: str) -> int:
    """Parse a size string into whole bytes.

    Raises ``ValueError`` for an empty or unrecognised value. Callers that
    must not fail on garbage use :func:`parse_size_bytes_or` instead.
    """
    text = (value or "").strip().upper()
    if not text:
        raise ValueError(f"unparsable size: {value!r}")

    factor = 1
    for suffix in _SUFFIXES:
        if text.endswith(suffix):
            factor = _UNITS[suffix]
            text = text[: -len(suffix)].strip()
            break

    try:
        return int(float(text) * factor)
    except ValueError:
        raise ValueError(f"unparsable size: {value!r}") from None


def parse_size_bytes_or(value: str, default: int = 0) -> int:
    """Parse a size string into bytes, returning `default` if it cannot be."""
    try:
        return parse_size_bytes(value)
    except ValueError:
        return default
