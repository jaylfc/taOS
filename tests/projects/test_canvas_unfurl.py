import pytest
from unittest.mock import AsyncMock, patch

from tinyagentos.projects.canvas.unfurl import fetch_link_metadata


@pytest.mark.asyncio
async def test_unfurl_extracts_og_tags():
    html = """<html><head>
        <meta property="og:title" content="My Title" />
        <meta property="og:description" content="My Description" />
        <meta property="og:image" content="https://x.example/i.png" />
        <link rel="icon" href="https://x.example/f.ico" />
    </head></html>"""
    with patch("tinyagentos.projects.canvas.unfurl._http_get", AsyncMock(return_value=(200, html))):
        meta = await fetch_link_metadata("https://x.example/page")
    assert meta["title"] == "My Title"
    assert meta["description"] == "My Description"
    assert meta["preview_image_url"] == "https://x.example/i.png"
    assert meta["favicon_url"] == "https://x.example/f.ico"
    assert meta["url"] == "https://x.example/page"


@pytest.mark.asyncio
async def test_unfurl_falls_back_to_html_title():
    html = "<html><head><title>Plain Title</title></head><body></body></html>"
    with patch("tinyagentos.projects.canvas.unfurl._http_get", AsyncMock(return_value=(200, html))):
        meta = await fetch_link_metadata("https://x.example/page")
    assert meta["title"] == "Plain Title"
    assert meta["description"] == ""


@pytest.mark.asyncio
async def test_unfurl_handles_non_200():
    with patch("tinyagentos.projects.canvas.unfurl._http_get", AsyncMock(return_value=(500, ""))):
        meta = await fetch_link_metadata("https://x.example/page")
    assert meta["title"] == "https://x.example/page"
    assert meta["description"] == ""


@pytest.mark.asyncio
async def test_unfurl_handles_timeout():
    with patch("tinyagentos.projects.canvas.unfurl._http_get",
               AsyncMock(side_effect=TimeoutError)):
        meta = await fetch_link_metadata("https://x.example/page")
    assert meta["title"] == "https://x.example/page"
