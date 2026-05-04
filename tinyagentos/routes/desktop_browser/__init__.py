"""BrowserApp v2 backend module group.

Exposes the FastAPI router that future PRs mount routes onto. Stores
live in `store.py`. Schema in `schema.py`. Crypto in `crypto.py`.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

# Side-effect import: registers GET /api/desktop/browser/proxy on `router`.
# Must come AFTER `router` is defined.
from tinyagentos.routes.desktop_browser import proxy as _proxy  # noqa: E402,F401
from tinyagentos.routes.desktop_browser import windows as _windows  # noqa: E402,F401
from tinyagentos.routes.desktop_browser import suggest as _suggest  # noqa: E402,F401
from tinyagentos.routes.desktop_browser import profile_routes as _profile_routes  # noqa: E402,F401
