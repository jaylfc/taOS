"""Verify the frontend ConsentActions PROJECT_SCOPES set stays in sync with the
backend _PROJECT_SCOPES. A drift here silently reintroduces the 400 bug the
consent picker fix is meant to close."""

import re
from pathlib import Path

from tinyagentos.routes.agent_auth_requests import _PROJECT_SCOPES


def test_consent_actions_project_scopes_match_backend():
    ts_file = Path("desktop/src/components/ConsentActions.tsx").read_text()
    match = re.search(r"const PROJECT_SCOPES = new Set\(\[(.*?)\]\)", ts_file, re.DOTALL)
    assert match, "PROJECT_SCOPES constant not found in ConsentActions.tsx"
    scopes_str = match.group(1)
    ts_scopes = {s.strip().strip('"').strip("'") for s in scopes_str.split(",") if s.strip()}
    assert ts_scopes == _PROJECT_SCOPES, (
        f"ConsentActions.tsx PROJECT_SCOPES drifted from backend _PROJECT_SCOPES: "
        f"TS={sorted(ts_scopes)}, Python={sorted(_PROJECT_SCOPES)}"
    )
