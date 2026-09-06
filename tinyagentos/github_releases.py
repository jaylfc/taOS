"""GitHub Releases fetch + parse helpers.

Pure parsing split out so it's testable without network. `fetch_latest_release`
uses the app's shared httpx client and returns the parsed dict shape.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ReleaseAssetNotFoundError(LookupError):
    """The release has no asset matching the expected pattern."""


def parse_release(raw: dict, *, asset_pattern: str, arch: str) -> dict[str, Any]:
    """Extract the fields we need from a GitHub Releases API response.

    Raises ReleaseAssetNotFoundError if no asset matches `asset_pattern`
    formatted with `arch`.
    """
    full_sha = raw["target_commitish"]
    expected_name = asset_pattern.format(arch=arch)
    asset = next(
        (a for a in raw.get("assets", []) if a.get("name") == expected_name),
        None,
    )
    if asset is None:
        raise ReleaseAssetNotFoundError(
            f"asset {expected_name!r} not found in release {raw.get('tag_name')!r}"
        )
    return {
        "tag": raw["tag_name"],
        "full_sha": full_sha,
        "sha": full_sha[:7],
        "published_at": raw.get("published_at"),
        "asset_url": asset["browser_download_url"],
    }


async def fetch_latest_release(
    manifest: dict, http_client, *, arch: str,
) -> dict[str, Any]:
    """Fetch /repos/{owner}/{repo}/releases/latest and parse it."""
    source = manifest["release_source"]
    if not source.startswith("github:"):
        raise ValueError(f"unsupported release_source scheme: {source!r}")
    owner_repo = source[len("github:"):]
    url = f"https://api.github.com/repos/{owner_repo}/releases/latest"
    resp = await http_client.get(url, headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    return parse_release(
        resp.json(),
        asset_pattern=manifest["release_asset_pattern"],
        arch=arch,
    )
