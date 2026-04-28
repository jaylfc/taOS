from __future__ import annotations
import secrets

ID_PREFIXES = ("prj", "tsk", "cmt", "rel", "cve")
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def new_id(prefix: str) -> str:
    if prefix not in ID_PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix}")
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"{prefix}-{suffix}"
