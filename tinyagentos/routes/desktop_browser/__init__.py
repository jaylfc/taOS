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
from tinyagentos.routes.desktop_browser import extract as _extract  # noqa: E402,F401
from tinyagentos.routes.desktop_browser import agent_pin_routes as _agent_pin_routes  # noqa: E402,F401
from tinyagentos.routes.desktop_browser import copilot_ws as _copilot_ws  # noqa: E402,F401
from tinyagentos.routes.desktop_browser import copilot_agent_ws as _copilot_agent_ws  # noqa: E402,F401
from tinyagentos.routes.desktop_browser import capability_routes as _capability_routes  # noqa: E402,F401
from tinyagentos.routes.desktop_browser import bookmark_routes as _bookmark_routes  # noqa: E402,F401
