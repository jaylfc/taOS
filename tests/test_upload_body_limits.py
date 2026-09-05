"""An oversized upload must be cut off while the body is still arriving.

FastAPI resolves an ``UploadFile`` parameter by parsing the multipart body
*before* the route handler runs, and Starlette's ``max_part_size`` guard covers
only non-file parts (``formparsers.MultiPartParser.on_part_data`` skips the
check when the part has a filename). So a route-level ``read(cap + 1)`` returns
413 only after the whole hostile body has already been spooled to temporary
storage -- the exact resource the cap exists to protect.

These tests therefore assert on bytes reaching the spool, not just on the
status code: the status code was already 413 before the request-body cap
existed.
"""

import io
import zipfile

import pytest
import yaml

_KIB = 1024
_MIB = 1024 * 1024
_CAP = 64 * _KIB

BOUNDARY = "taosuploadboundary"
_CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"

MANIFEST = {"id": "matrix-terminal", "name": "Matrix Terminal", "version": "1.0.0"}


def _multipart(field: str, filename: str, payload: bytes) -> bytes:
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    return head + payload + f"\r\n--{BOUNDARY}--\r\n".encode()


@pytest.fixture
def spooled(monkeypatch):
    """Count every byte the multipart parser writes into an upload's spool."""
    import starlette.formparsers as formparsers

    counter = {"total": 0}
    real = formparsers.SpooledTemporaryFile

    class _CountingSpool(real):  # type: ignore[misc, valid-type]
        def write(self, data):  # noqa: D102
            counter["total"] += len(data)
            return super().write(data)

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", _CountingSpool)
    return counter


@pytest.mark.asyncio
async def test_theme_install_never_spools_more_than_the_cap(client, monkeypatch, spooled):
    """A declared oversize body is refused before the parser stores it."""
    import tinyagentos.routes.themes as themes_routes

    monkeypatch.setattr(themes_routes, "_MAX_THEME_PACKAGE_BYTES", _CAP)
    resp = await client.post(
        "/api/themes/install",
        files={"package": ("matrix.taostheme", b"\0" * (4 * _MIB), "application/zip")},
    )
    assert resp.status_code == 413, resp.text
    assert spooled["total"] <= _CAP, (
        f"{spooled['total']} bytes reached the upload spool for a {_CAP}-byte cap"
    )


@pytest.mark.asyncio
async def test_restore_stops_a_chunked_body_at_the_cap(client, monkeypatch, spooled):
    """No Content-Length to trust: the cap has to count the arriving body."""
    import tinyagentos.routes.settings as settings_routes

    monkeypatch.setattr(settings_routes, "_MAX_BACKUP_BYTES", _CAP)
    body = _multipart("file", "backup.tar.gz", b"\0" * (4 * _MIB))

    async def _stream():
        for i in range(0, len(body), 64 * _KIB):
            yield body[i : i + 64 * _KIB]

    resp = await client.post(
        "/api/restore", content=_stream(), headers={"content-type": _CONTENT_TYPE}
    )
    assert resp.status_code == 413, resp.text
    # The middleware answers http.disconnect for the very chunk that would push
    # received over cap, so that chunk is never forwarded to the parser: the
    # spool should never see more than the cap itself, chunked body or not.
    assert spooled["total"] <= _CAP, (
        f"{spooled['total']} bytes reached the upload spool for a {_CAP}-byte cap"
    )


@pytest.mark.asyncio
async def test_an_upload_within_the_cap_still_installs(client):
    """The cap must not turn a legitimate package away."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("theme.yaml", yaml.safe_dump(MANIFEST))
        z.writestr("assets/wall.png", "x")
    resp = await client.post(
        "/api/themes/install",
        files={"package": ("matrix.taostheme", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["theme_id"] == "matrix-terminal"
