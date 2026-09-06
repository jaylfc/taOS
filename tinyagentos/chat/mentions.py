"""@mention parser for multi-agent chat routing.

Produces a MentionSet describing which agents a message directly addresses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)(?![A-Za-z0-9_])")
_SPECIAL_ALL = "all"
_SPECIAL_HUMANS = "humans"


@dataclass(frozen=True)
class MentionSet:
    explicit: tuple[str, ...]
    all: bool
    humans: bool


def parse_mentions(text: str, members: list[str]) -> MentionSet:
    if not text:
        return MentionSet(explicit=(), all=False, humans=False)
    canonical_members = {m.lower() for m in members}
    raw = [m.group(1).lower() for m in _MENTION_RE.finditer(text)]
    has_all = _SPECIAL_ALL in raw
    has_humans = _SPECIAL_HUMANS in raw
    explicit = {m for m in raw if m not in (_SPECIAL_ALL, _SPECIAL_HUMANS) and m in canonical_members}
    return MentionSet(
        explicit=tuple(sorted(explicit)),
        all=has_all,
        humans=has_humans,
    )
