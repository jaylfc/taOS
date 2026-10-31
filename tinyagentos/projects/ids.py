from __future__ import annotations

import re
import secrets
import time

ID_PREFIXES = ("prj", "tsk", "cmt", "rel", "cve", "dec", "doc", "ent", "lst", "rev", "cs", "rtn", "elm", "note", "str", "crq", "cki")
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def new_id(prefix: str) -> str:
    if prefix not in ID_PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix}")
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"{prefix}-{suffix}"


def friendly_project_id(slug: str, now: float | None = None) -> str:
    """Generate a human-readable project ID from a slug and current month.

    Returns prj-{stem}-{yymm} where:
    - stem is the slug lower-cased, keeping only [a-z0-9-], trimmed of
      leading/trailing hyphens, cut to 20 chars.
    - yymm is the current year/month as two-digit strings (e.g., 2510 for Oct 2025)

    If the stem is empty, falls back to a random ID.
    """
    if now is None:
        now = time.time()
    stem = slug.lower()
    stem = re.sub(r"[^a-z0-9-]", "", stem)
    stem = stem.strip("-")
    if not stem:
        return new_id("prj")
    stem = stem[:20]
    yymm = time.strftime("%y%m", time.gmtime(now))
    return f"prj-{stem}-{yymm}"
