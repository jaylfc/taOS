"""BrowserApp v2 backend module group.

Exposes the FastAPI router that future PRs mount routes onto. Stores
live in `store.py`. Schema in `schema.py`. Crypto in `crypto.py`.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
