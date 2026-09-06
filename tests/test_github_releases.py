import pytest
from tinyagentos.github_releases import parse_release, ReleaseAssetNotFoundError

SAMPLE = {
    "tag_name": "20260418T133712",
    "target_commitish": "9bab2e347aaa11e7e646b49dc358d00d01b1d21d",
    "published_at": "2026-04-18T19:41:07Z",
    "assets": [
        {"name": "openclaw-taos-fork-linux-x86_64.tgz", "browser_download_url": "https://x.com/x86_64.tgz"},
        {"name": "openclaw-taos-fork-linux-aarch64.tgz", "browser_download_url": "https://x.com/aarch64.tgz"},
    ],
}

def test_parse_release_extracts_tag_and_shas():
    p = parse_release(SAMPLE, asset_pattern="openclaw-taos-fork-linux-{arch}.tgz", arch="x86_64")
    assert p["tag"] == "20260418T133712"
    assert p["sha"] == "9bab2e3"
    assert p["full_sha"] == "9bab2e347aaa11e7e646b49dc358d00d01b1d21d"
    assert p["published_at"] == "2026-04-18T19:41:07Z"
    assert p["asset_url"] == "https://x.com/x86_64.tgz"

def test_parse_release_picks_arch_specific_asset():
    p = parse_release(SAMPLE, asset_pattern="openclaw-taos-fork-linux-{arch}.tgz", arch="aarch64")
    assert p["asset_url"] == "https://x.com/aarch64.tgz"

def test_parse_release_raises_when_asset_missing():
    with pytest.raises(ReleaseAssetNotFoundError):
        parse_release(SAMPLE, asset_pattern="openclaw-taos-fork-linux-{arch}.tgz", arch="riscv64")
