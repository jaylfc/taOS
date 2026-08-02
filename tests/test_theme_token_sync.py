"""Backend/frontend theme-token allowlist drift guard.

The original shell-scrim/shell-subtle defect (#2219 review): tokens were added
to the client ALLOWED_TOKENS but not to the backend _COLOR_TOKENS, so valid
theme packages passed the client and were rejected server-side. This test pins
the two lists together: every color token the client allows must be accepted
by the backend schema, and vice versa.
"""
import re
from pathlib import Path

from tinyagentos.themes.schema import _COLOR_TOKENS

REPO_ROOT = Path(__file__).resolve().parent.parent
THEME_CONFIG = REPO_ROOT / "desktop" / "src" / "theme" / "theme-config.ts"


def _client_color_tokens() -> set[str]:
    src = THEME_CONFIG.read_text(encoding="utf-8")
    m = re.search(r"ALLOWED_TOKENS[^=]*=\s*new Set<string>\(\[(.*?)\]\)", src, re.S)
    assert m, "ALLOWED_TOKENS literal not found in theme-config.ts"
    tokens = set(re.findall(r"[\"']([^\"']+)[\"']", m.group(1)))
    return {t for t in tokens if t.startswith("--color-")}


def test_backend_accepts_every_client_color_token():
    missing = _client_color_tokens() - set(_COLOR_TOKENS)
    assert not missing, (
        f"client-allowed color tokens rejected by backend schema: {sorted(missing)}"
    )


def test_client_allows_every_backend_color_token():
    missing = set(_COLOR_TOKENS) - _client_color_tokens()
    assert not missing, (
        f"backend-accepted color tokens missing from client allowlist: {sorted(missing)}"
    )
