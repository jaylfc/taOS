"""HTTP header helpers for security-sensitive responses."""
from __future__ import annotations

def no_store(response) -> None:
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Pragma", "no-cache")
