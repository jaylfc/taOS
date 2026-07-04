"""Tests for the tier-aware image-editing routes (routes/images_edit.py)."""
import base64
import io
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from PIL import Image

from tinyagentos.app import create_app
from tinyagentos.routes.images_edit import (
    EditRequest,
    FluxFillClient,
    _get_edit_backend,
    _require_image,
    edit_capabilities,
    edit_image,
)


def _png_b64(width=8, height=8, color=(20, 120, 200)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _backend(name, btype, priority=10):
    return SimpleNamespace(name=name, type=btype, priority=priority, url=f"http://{name}")


class _FakeCatalog:
    def __init__(self, by_cap):
        self._by_cap = by_cap

    def backends_with_capability(self, capability):
        return list(self._by_cap.get(capability, []))


def _request_with_catalog(catalog):
    state = SimpleNamespace(backend_catalog=catalog)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_edit_routes_registered():
    """The three edit endpoints + the capabilities probe exist on the app."""
    app = create_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/images/edit" in paths
    assert "/api/images/remove-bg" in paths
    assert "/api/images/upscale" in paths
    assert "/api/images/edit/capabilities" in paths


def test_tier_preference_quality_prefers_flux_fill():
    """quality tier prefers the GPU diffusion backend (flux-fill) over iopaint."""
    catalog = _FakeCatalog(
        {"image-editing": [_backend("io", "iopaint"), _backend("flux", "flux-fill")]}
    )
    req = _request_with_catalog(catalog)
    url, btype, name = _get_edit_backend(req, "image-editing", "quality")
    assert btype == "flux-fill"
    assert name == "flux"


def test_tier_preference_fast_prefers_iopaint():
    """fast tier prefers the CPU/NPU backend (iopaint) over flux-fill."""
    catalog = _FakeCatalog(
        {"image-editing": [_backend("flux", "flux-fill"), _backend("io", "iopaint")]}
    )
    req = _request_with_catalog(catalog)
    url, btype, name = _get_edit_backend(req, "image-editing", "fast")
    assert btype == "iopaint"
    assert name == "io"


def test_tier_preference_falls_back_when_preferred_absent():
    """quality tier still resolves iopaint when no flux-fill backend exists."""
    catalog = _FakeCatalog({"image-editing": [_backend("io", "iopaint")]})
    req = _request_with_catalog(catalog)
    url, btype, name = _get_edit_backend(req, "image-editing", "quality")
    assert btype == "iopaint"


def test_no_backend_returns_none():
    """No healthy backend for the capability → graceful None."""
    catalog = _FakeCatalog({})
    req = _request_with_catalog(catalog)
    assert _get_edit_backend(req, "image-editing", "fast") is None


def test_no_catalog_returns_none():
    """No live catalog (scheduler not started) → None, never raises."""
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(backend_catalog=None)))
    assert _get_edit_backend(req, "upscale", "fast") is None


# --------------------------------------------------------------------------- #
#  FluxFillClient (A1111 img2img inpaint)                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_flux_fill_inpaint_posts_a1111_img2img():
    """inpaint() POSTs base64 init_images + mask to /sdapi/v1/img2img and
    decodes images[0] from the JSON response back to PNG bytes."""
    result_b64 = _png_b64(color=(255, 0, 0))
    route = respx.post("http://flux:7864/sdapi/v1/img2img").mock(
        return_value=httpx.Response(200, json={"images": [result_b64]})
    )

    client = FluxFillClient("http://flux:7864")
    out = await client.inpaint(_png_b64(width=37, height=53), _png_b64(width=37, height=53, color=(0, 0, 0)), prompt="a cat")

    assert route.called
    sent = route.calls.last.request
    import json

    payload = json.loads(sent.content)
    assert sent.url.path == "/sdapi/v1/img2img"
    assert isinstance(payload["init_images"], list) and len(payload["init_images"]) == 1
    # init_images[0] and mask are valid base64 PNGs.
    assert Image.open(io.BytesIO(base64.b64decode(payload["init_images"][0]))).format == "PNG"
    assert Image.open(io.BytesIO(base64.b64decode(payload["mask"]))).format == "PNG"
    # width/height are pinned to the source image so the server does not fall
    # back to a default 512x512 size and wrongly resize the result.
    assert (payload["width"], payload["height"]) == (37, 53)
    assert payload["prompt"] == "a cat"
    assert payload["cfg_scale"] == pytest.approx(30.0)
    # Returned bytes are the decoded result PNG.
    assert out == base64.b64decode(result_b64)


@pytest.mark.asyncio
@respx.mock
async def test_flux_fill_outpaint_pads_canvas():
    """Outpaint pre-pads the init image + mask larger than the source, since
    sd.cpp img2img does not natively grow the canvas."""
    src_b64 = _png_b64(width=16, height=16)
    respx.post("http://flux:7864/sdapi/v1/img2img").mock(
        return_value=httpx.Response(200, json={"images": [_png_b64()]})
    )

    client = FluxFillClient("http://flux:7864")
    await client.inpaint(src_b64, _png_b64(width=16, height=16, color=(0, 0, 0)), outpaint=True)

    import json

    payload = json.loads(respx.calls.last.request.content)
    padded = Image.open(io.BytesIO(base64.b64decode(payload["init_images"][0])))
    mask = Image.open(io.BytesIO(base64.b64decode(payload["mask"])))
    assert padded.size[0] > 16 and padded.size[1] > 16
    assert mask.size == padded.size
    # width/height match the padded canvas actually sent, not the 16x16 source.
    assert (payload["width"], payload["height"]) == padded.size
    # The outpaint mask border is white (paint) and the centre is black (keep).
    assert mask.convert("L").getpixel((0, 0)) == 255
    assert mask.convert("L").getpixel((mask.size[0] // 2, mask.size[1] // 2)) == 0


@pytest.mark.asyncio
@respx.mock
async def test_flux_fill_missing_images_raises():
    """A response without images raises, which edit_image maps to an error."""
    respx.post("http://flux:7864/sdapi/v1/img2img").mock(
        return_value=httpx.Response(200, json={"images": []})
    )
    client = FluxFillClient("http://flux:7864")
    with pytest.raises(Exception):
        await client.inpaint(_png_b64(), _png_b64())


# --------------------------------------------------------------------------- #
#  edit_image dispatch → FluxFillClient on the quality tier                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_edit_image_quality_routes_to_flux_fill(tmp_path):
    """A healthy flux-fill backend on the quality tier dispatches to
    FluxFillClient (POST /sdapi/v1/img2img) and saves the returned bytes."""
    # Workspace: config_path.parent/workspace/images/generated/
    images_dir = tmp_path / "workspace" / "images" / "generated"
    images_dir.mkdir(parents=True)
    src_bytes = base64.b64decode(_png_b64())
    (images_dir / "src.png").write_bytes(src_bytes)

    result_b64 = _png_b64(color=(7, 8, 9))
    route = respx.post("http://flux/sdapi/v1/img2img").mock(
        return_value=httpx.Response(200, json={"images": [result_b64]})
    )

    catalog = _FakeCatalog({"image-editing": [_backend("flux", "flux-fill")]})
    state = SimpleNamespace(backend_catalog=catalog, config_path=str(tmp_path / "config.json"))
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    body = EditRequest(image_ref="src.png", op="inpaint", mask=_png_b64(), prompt="sky", tier="quality")
    result = await edit_image(request, body)

    assert route.called
    assert result["status"] == "edited"
    saved = (images_dir / result["filename"]).read_bytes()
    assert saved == base64.b64decode(result_b64)


@pytest.mark.asyncio
@respx.mock
async def test_edit_image_quality_falls_back_to_iopaint(tmp_path):
    """With no flux-fill backend, the quality tier still uses iopaint
    (POST /api/v1/inpaint), leaving the fast path untouched."""
    images_dir = tmp_path / "workspace" / "images" / "generated"
    images_dir.mkdir(parents=True)
    (images_dir / "src.png").write_bytes(base64.b64decode(_png_b64()))

    result_png = base64.b64decode(_png_b64(color=(1, 2, 3)))
    route = respx.post("http://io/api/v1/inpaint").mock(
        return_value=httpx.Response(200, content=result_png, headers={"content-type": "image/png"})
    )

    catalog = _FakeCatalog({"image-editing": [_backend("io", "iopaint")]})
    state = SimpleNamespace(backend_catalog=catalog, config_path=str(tmp_path / "config.json"))
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    body = EditRequest(image_ref="src.png", op="inpaint", mask=_png_b64(), tier="quality")
    result = await edit_image(request, body)

    assert route.called
    assert (images_dir / result["filename"]).read_bytes() == result_png


# --------------------------------------------------------------------------- #
#  IOPaint hardening: non-image 200, data-URI mask strip, backend surfacing    #
# --------------------------------------------------------------------------- #
def _edit_request_with_workspace(tmp_path, backends):
    """Build a request whose workspace holds a single src.png + return its dir."""
    images_dir = tmp_path / "workspace" / "images" / "generated"
    images_dir.mkdir(parents=True)
    (images_dir / "src.png").write_bytes(base64.b64decode(_png_b64()))
    catalog = _FakeCatalog({"image-editing": list(backends)})
    state = SimpleNamespace(backend_catalog=catalog, config_path=str(tmp_path / "config.json"))
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    return request, images_dir


@pytest.mark.asyncio
@respx.mock
async def test_iopaint_non_image_200_is_an_error_not_a_saved_png(tmp_path):
    """IOPaint answering 200 with a JSON error body must NOT be saved as a png;
    it routes to an error response so the user doesn't get a fake success."""
    respx.post("http://io/api/v1/inpaint").mock(
        return_value=httpx.Response(
            200,
            json={"errors": "model not loaded"},
            headers={"content-type": "application/json"},
        )
    )
    request, images_dir = _edit_request_with_workspace(tmp_path, [_backend("io", "iopaint")])
    body = EditRequest(image_ref="src.png", op="inpaint", mask=_png_b64(), tier="fast")
    result = await edit_image(request, body)

    # An error JSONResponse, not a saved-png success dict.
    from fastapi.responses import JSONResponse as _JR

    assert isinstance(result, _JR)
    # No new png was written (only the source remains).
    pngs = sorted(p.name for p in images_dir.glob("*.png"))
    assert pngs == ["src.png"]


@pytest.mark.asyncio
@respx.mock
async def test_iopaint_strips_data_uri_mask(tmp_path):
    """A data-URI mask is stripped to bare base64 before hitting IOPaint
    (previously only FluxFill stripped it, so a data-URI corrupted IOPaint)."""
    result_png = base64.b64decode(_png_b64(color=(4, 5, 6)))
    route = respx.post("http://io/api/v1/inpaint").mock(
        return_value=httpx.Response(200, content=result_png, headers={"content-type": "image/png"})
    )
    request, _ = _edit_request_with_workspace(tmp_path, [_backend("io", "iopaint")])

    bare = _png_b64(color=(0, 0, 0))
    data_uri_mask = f"data:image/png;base64,{bare}"
    body = EditRequest(image_ref="src.png", op="inpaint", mask=data_uri_mask, tier="fast")
    await edit_image(request, body)

    import json as _json

    payload = _json.loads(route.calls.last.request.content)
    assert payload["mask"] == bare  # prefix dropped
    assert not payload["mask"].startswith("data:")
    assert not payload["image"].startswith("data:")


@pytest.mark.asyncio
@respx.mock
async def test_edit_response_surfaces_backend_and_degraded(tmp_path):
    """The edit response carries the backend type that actually ran, and flags
    a silent tier downgrade (quality requested, iopaint fell back -> degraded)."""
    result_png = base64.b64decode(_png_b64(color=(1, 1, 1)))
    respx.post("http://io/api/v1/inpaint").mock(
        return_value=httpx.Response(200, content=result_png, headers={"content-type": "image/png"})
    )
    request, _ = _edit_request_with_workspace(tmp_path, [_backend("io", "iopaint")])

    body = EditRequest(image_ref="src.png", op="inpaint", mask=_png_b64(), tier="quality")
    result = await edit_image(request, body)

    assert result["backend"] == "iopaint"
    assert result["degraded"] is True  # quality asked for flux-fill, got iopaint


@pytest.mark.asyncio
@respx.mock
async def test_edit_response_not_degraded_when_tier_satisfied(tmp_path):
    """When the chosen backend matches the tier's primary, degraded is False."""
    result_png = base64.b64decode(_png_b64(color=(2, 2, 2)))
    respx.post("http://io/api/v1/inpaint").mock(
        return_value=httpx.Response(200, content=result_png, headers={"content-type": "image/png"})
    )
    request, _ = _edit_request_with_workspace(tmp_path, [_backend("io", "iopaint")])

    body = EditRequest(image_ref="src.png", op="inpaint", mask=_png_b64(), tier="fast")
    result = await edit_image(request, body)

    assert result["backend"] == "iopaint"
    assert result["degraded"] is False  # fast tier prefers iopaint


def test_require_image_accepts_image_magic_without_image_content_type():
    """A valid image whose content-type is not image/* (e.g. octet-stream) is
    accepted by its magic signature rather than rejected as an error."""
    png_bytes = base64.b64decode(_png_b64())
    resp = httpx.Response(
        200, content=png_bytes, headers={"content-type": "application/octet-stream"}
    )
    assert _require_image(resp) == png_bytes


def test_require_image_rejects_json_text_200():
    """A JSON/text 200 with no image magic is still rejected (IOPaint error)."""
    resp = httpx.Response(
        200, json={"errors": "model not loaded"}, headers={"content-type": "application/json"}
    )
    with pytest.raises(RuntimeError, match="non-image response"):
        _require_image(resp)


@pytest.mark.asyncio
@respx.mock
async def test_save_oserror_maps_to_clean_error(tmp_path):
    """A disk write failure in _save_result surfaces as a 'Could not save result'
    error, distinct from a backend-unreachable response (not a bare 500)."""
    result_png = base64.b64decode(_png_b64(color=(3, 3, 3)))
    respx.post("http://io/api/v1/inpaint").mock(
        return_value=httpx.Response(200, content=result_png, headers={"content-type": "image/png"})
    )
    request, images_dir = _edit_request_with_workspace(tmp_path, [_backend("io", "iopaint")])

    import json as _json

    # Force the result write to fail with OSError.
    real_write_bytes = Path.write_bytes

    def _boom(self, data):
        if self.parent == images_dir and self.suffix == ".png" and self.name != "src.png":
            raise OSError(28, "No space left on device")
        return real_write_bytes(self, data)

    import unittest.mock as mock

    body = EditRequest(image_ref="src.png", op="inpaint", mask=_png_b64(), tier="fast")
    with mock.patch.object(Path, "write_bytes", _boom):
        result = await edit_image(request, body)

    from fastapi.responses import JSONResponse as _JR

    assert isinstance(result, _JR)
    assert result.status_code == 500
    payload = _json.loads(bytes(result.body))
    assert "Could not save result" in payload["error"]


# --------------------------------------------------------------------------- #
#  edit_capabilities: per-tier health so the UI can hide Quality when only     #
#  the fast (iopaint) backend is actually healthy, instead of letting the     #
#  frontend offer a Quality tier that silently downgrades.                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_capabilities_both_tiers_healthy_when_flux_fill_present():
    """Both flux-fill and iopaint healthy -> quality and fast both report healthy."""
    catalog = _FakeCatalog(
        {"image-editing": [_backend("flux", "flux-fill"), _backend("io", "iopaint")]}
    )
    request = _request_with_catalog(catalog)
    result = await edit_capabilities(request)

    assert result["image_editing"] is True
    assert result["image_editing_tiers"] == {"quality": True, "fast": True}


@pytest.mark.asyncio
async def test_capabilities_quality_unhealthy_when_only_iopaint_present():
    """Only iopaint healthy -> quality reports unhealthy (would silently
    downgrade to the prompt-ignoring eraser), fast still reports healthy."""
    catalog = _FakeCatalog({"image-editing": [_backend("io", "iopaint")]})
    request = _request_with_catalog(catalog)
    result = await edit_capabilities(request)

    assert result["image_editing"] is True  # some backend is healthy
    assert result["image_editing_tiers"] == {"quality": False, "fast": True}


@pytest.mark.asyncio
async def test_capabilities_fast_healthy_when_only_flux_fill_present():
    """Only flux-fill healthy -> fast reports healthy, because the router
    (via _get_edit_backend's fallback) serves a fast request on flux-fill,
    which still honours the prompt. The probe must not disable a tier the
    router would actually serve. Quality is healthy too (its primary ran)."""
    catalog = _FakeCatalog({"image-editing": [_backend("flux", "flux-fill")]})
    request = _request_with_catalog(catalog)
    result = await edit_capabilities(request)

    assert result["image_editing"] is True
    assert result["image_editing_tiers"] == {"quality": True, "fast": True}


@pytest.mark.asyncio
async def test_capabilities_no_catalog_reports_all_unhealthy():
    """No live catalog -> every capability and tier reports unhealthy, never raises."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(backend_catalog=None)))
    result = await edit_capabilities(request)

    assert result["image_editing"] is False
    assert result["image_editing_tiers"] == {"quality": False, "fast": False}
