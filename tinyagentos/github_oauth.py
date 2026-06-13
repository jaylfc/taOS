"""GitHub OAuth Device Flow configuration.

taOS is self-hosted; instances have no fixed OAuth callback URL, so we use
GitHub's OAuth *Device Flow*, which needs only the public Client ID (no client
secret). Public OAuth Client IDs are safe to ship in source — they are
exposed to every browser that starts the flow and carry no privilege on their
own.
"""
from __future__ import annotations

import os

# The single taOS OAuth App Client ID. Public and safe in source.
# Override per-instance with the GITHUB_OAUTH_CLIENT_ID env var.
DEFAULT_CLIENT_ID = "Ov23licVGSIqagQLXAqb"

# Device-flow scopes: repo access + read the authenticated user's profile.
DEVICE_FLOW_SCOPE = "repo read:user"

# GitHub endpoints.
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

# device_code grant type per RFC 8628.
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def client_id() -> str:
    """Return the configured GitHub OAuth Client ID."""
    return os.environ.get("GITHUB_OAUTH_CLIENT_ID", DEFAULT_CLIENT_ID)
