"""Local speech-to-text through the gateway: ``POST /api/llm/v1/audio/transcriptions``.

The real controller app (``create_app`` on a tmp data dir) with a gateway key
as the credential; the daemon is a real throwaway HTTP server on an ephemeral
127.0.0.1 port, so the bytes that reach it are the bytes we assert on.
The malformed and failing cases come first, the happy paths last.
"""
from __future__ import annotations

import io
import json
import socket
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.llm_gateway import stt
from tinyagentos.llm_gateway.auth import mint_gateway_key
from tinyagentos.llm_gateway.errors import GatewayError

URL = "/api/llm/v1/audio/transcriptions"
MODEL = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"


# --- fixtures ---------------------------------------------------------------


class Daemon:
    """A stand-in taos-sttd that records every request it gets."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.body = json.dumps({"text": "hello world"}).encode()
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                daemon.requests.append({"path": self.path, "headers": dict(self.headers),
                                        "body": self.rfile.read(length)})
                self.send_response(daemon.status)
                self.send_header("Content-Length", str(len(daemon.body)))
                self.end_headers()
                self.wfile.write(daemon.body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def daemon():
    d = Daemon()
    yield d
    d.close()


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def write_manifest(data_dir, port, **extra):
    path = data_dir / "voice" / "stt" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"engine": "sherpa-onnx", "model": MODEL, "port": port, **extra}))
    return path


@pytest.fixture(autouse=True)
def _no_manifest_override(monkeypatch):
    monkeypatch.delenv("TAOS_STT_MANIFEST", raising=False)


@pytest_asyncio.fixture
async def gw(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    key = mint_gateway_key(bound_to="stt-agent", kind="agent",
                           allowed_models=[stt.STT_ALIAS, MODEL], data_dir=tmp_data_dir)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {key}"}) as c:
        yield c, tmp_data_dir, app


def make_wav(pcm: bytes = b"\x01\x00" * 1600, *, channels=1, rate=16000, width=2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def riff(fmt_tag=1, channels=1, rate=16000, width=2, data=b"\x00\x00" * 100, data_size=None) -> bytes:
    """A hand-built WAV, for the headers ``wave`` will not write."""
    fmt = struct.pack("<HHIIHH", fmt_tag, channels, rate, rate * channels * width,
                      channels * width, width * 8)
    size = len(data) if data_size is None else data_size
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def multipart(parts: list[tuple[str, bytes, str | None]]) -> tuple[bytes, str]:
    b = "XXboundaryXX"
    out = b""
    for name, value, filename in parts:
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        out += f"--{b}\r\nContent-Disposition: {disp}\r\n\r\n".encode() + value + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


async def post(c, wav=None, model=MODEL, extra=(), omit=()):
    parts = []
    if "file" not in omit:
        parts.append(("file", make_wav() if wav is None else wav, "a.wav"))
    if "model" not in omit:
        parts.append(("model", model.encode(), None))
    parts += list(extra)
    body, ctype = multipart(parts)
    return await c.post(URL, content=body, headers={"content-type": ctype})


def code(resp) -> str:
    return resp.json()["error"]["code"]


# --- size caps and framing -------------------------------------------------


@pytest.mark.asyncio
async def test_content_length_over_cap_is_413_without_reading_the_body(gw, daemon):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    consumed = []

    async def body():
        consumed.append(1)
        yield b"x"

    resp = await c.post(URL, content=body(), headers={
        "content-length": str(stt.MAX_PCM_BYTES + 10 * 1024 * 1024),
        "content-type": "multipart/form-data; boundary=b"})
    assert resp.status_code == 413
    assert code(resp) == "request_too_large"
    assert not consumed
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_streamed_body_over_cap_without_content_length_is_413(gw, daemon):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    sent = []

    async def body():
        for _ in range(40):
            sent.append(1)
            yield b"x" * 65536  # 2.5 MiB in all; the cap is just over 960 kB

    req = c.build_request("POST", URL, content=body(),
                          headers={"content-type": "multipart/form-data; boundary=b"})
    assert "content-length" not in req.headers
    resp = await c.send(req)
    assert resp.status_code == 413
    assert code(resp) == "request_too_large"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_non_numeric_content_length_is_400(gw):
    c, _, _ = gw
    resp = await c.post(URL, content=b"x", headers={
        "content-length": "abc", "content-type": "multipart/form-data; boundary=b"})
    assert resp.status_code in (400, 413)  # h11 may reject it first; never a 200/500


# --- request shape ----------------------------------------------------------


@pytest.mark.asyncio
async def test_json_body_is_400(gw):
    c, _, _ = gw
    resp = await c.post(URL, json={"model": MODEL})
    assert resp.status_code == 400
    assert code(resp) == "invalid_request"


@pytest.mark.asyncio
async def test_multipart_without_boundary_is_400(gw):
    c, _, _ = gw
    resp = await c.post(URL, content=b"junk", headers={"content-type": "multipart/form-data"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unterminated_multipart_is_400(gw):
    c, _, _ = gw
    body, ctype = multipart([("model", MODEL.encode(), None), ("file", make_wav(), "a.wav")])
    resp = await c.post(URL, content=body[:-30], headers={"content-type": ctype})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_missing_file_is_400(gw):
    c, _, _ = gw
    resp = await post(c, omit=("file",))
    assert resp.status_code == 400
    assert "'file'" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_missing_model_is_400(gw):
    c, _, _ = gw
    resp = await post(c, omit=("model",))
    assert resp.status_code == 400
    assert "'model'" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_unknown_response_format_is_400(gw, daemon):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, extra=[("response_format", b"verbose_json", None)])
    assert resp.status_code == 400
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_duplicate_file_part_is_400(gw):
    c, _, _ = gw
    resp = await post(c, extra=[("file", make_wav(), "b.wav")])
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_no_credentials_is_401(gw, daemon):
    _, data_dir, app = gw
    write_manifest(data_dir, daemon.port)
    body, ctype = multipart([("file", make_wav(), "a.wav"), ("model", MODEL.encode(), None)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.post(URL, content=body, headers={"content-type": ctype})
    assert resp.status_code == 401
    assert daemon.requests == []


# --- model names and permission --------------------------------------------


@pytest.mark.asyncio
async def test_unknown_model_is_404(gw, daemon):
    c, data_dir, app = gw
    write_manifest(data_dir, daemon.port)
    key = mint_gateway_key(bound_to="wide", kind="agent", allowed_models=["whisper-1"],
                           data_dir=data_dir)
    body, ctype = multipart([("file", make_wav(), "a.wav"), ("model", b"whisper-1", None)])
    resp = await c.post(URL, content=body, headers={
        "content-type": ctype, "Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    assert code(resp) == "model_not_found"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_model_the_key_may_not_use_is_403(gw, daemon):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    key = mint_gateway_key(bound_to="chat-only", kind="agent", allowed_models=["qwen3-8b"],
                           data_dir=data_dir)
    body, ctype = multipart([("file", make_wav(), "a.wav"), ("model", stt.STT_ALIAS.encode(), None)])
    resp = await c.post(URL, content=body, headers={
        "content-type": ctype, "Authorization": f"Bearer {key}"})
    assert resp.status_code == 403
    assert code(resp) == "model_not_permitted"
    assert daemon.requests == []


# --- manifest ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_manifest_is_409_stt_not_installed(gw):
    c, _, _ = gw
    resp = await post(c, model=stt.STT_ALIAS)
    assert resp.status_code == 409
    assert code(resp) == "stt_not_installed"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "{not json", "[1, 2]", "null",
    json.dumps({"model": MODEL}),
    json.dumps({"model": MODEL, "port": "6975"}),
    json.dumps({"model": MODEL, "port": 0}),
    json.dumps({"model": MODEL, "port": 65536}),
    json.dumps({"model": MODEL, "port": True}),
    json.dumps({"port": 6975}),
    json.dumps({"model": 7, "port": 6975}),
    "",
])
async def test_corrupt_manifest_is_503_not_409(gw, content):
    c, data_dir, _ = gw
    path = data_dir / "voice" / "stt" / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(content)
    resp = await post(c, model=stt.STT_ALIAS)
    assert resp.status_code == 503
    assert code(resp) == "stt_unavailable"


@pytest.mark.asyncio
async def test_manifest_env_override_changes_the_file_read(gw, daemon, monkeypatch, tmp_path):
    c, data_dir, _ = gw
    write_manifest(data_dir, closed_port())  # the default path points at a dead port
    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps({"model": MODEL, "port": daemon.port}))
    monkeypatch.setenv("TAOS_STT_MANIFEST", str(other))
    assert stt.manifest_path(data_dir) == other
    resp = await post(c)
    assert resp.status_code == 200, resp.text
    assert len(daemon.requests) == 1


def test_default_manifest_path_is_under_the_data_dir(tmp_path):
    assert stt.manifest_path(tmp_path) == tmp_path / "voice" / "stt" / "manifest.json"


# --- WAV defects ------------------------------------------------------------


GOOD = make_wav()
WAV_DEFECTS = {
    "stereo": (make_wav(b"\x00\x00" * 200, channels=2), "mono"),
    "8khz": (make_wav(rate=8000), "16000 Hz"),
    "8bit": (make_wav(b"\x00" * 100, width=1), "16-bit"),
    "float": (riff(fmt_tag=3, width=4, data=b"\x00" * 400), "not PCM"),
    "truncated_data": (GOOD[:-1000], "truncated"),
    "truncated_header": (GOOD[:20], "truncated"),
    "zero_frames": (make_wav(b""), "no audio"),
    "odd_length": (riff(data=b"\x00" * 101), "odd"),
    "not_riff": (b"ID3" + b"\x00" * 100, "RIFF"),
    "not_wave": (b"RIFF" + struct.pack("<I", 4) + b"AVI " + b"\x00" * 50, "WAVE"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(WAV_DEFECTS))
async def test_each_wav_defect_is_400_with_its_own_message(gw, daemon, name):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    wav, fragment = WAV_DEFECTS[name]
    resp = await post(c, wav=wav)
    assert resp.status_code == 400, resp.text
    assert code(resp) == "invalid_audio"
    assert fragment in resp.json()["error"]["message"]
    assert daemon.requests == []


def test_wav_defect_messages_are_distinct():
    messages = []
    for wav, _ in WAV_DEFECTS.values():
        with pytest.raises(GatewayError) as exc:
            stt.wav_to_pcm(wav)
        messages.append(exc.value.message)
    # truncated_data and truncated_header may share their wording; nothing else.
    assert len(set(messages)) >= len(messages) - 1


# --- the daemon -------------------------------------------------------------


@pytest.mark.asyncio
async def test_daemon_down_on_a_closed_port_is_503(gw):
    c, data_dir, _ = gw
    write_manifest(data_dir, closed_port())
    resp = await post(c)
    assert resp.status_code == 503
    assert code(resp) == "stt_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["busy", "model loading"])
async def test_daemon_503_is_503_and_keeps_the_reason(gw, daemon, why):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.status, daemon.body = 503, json.dumps({"error": why}).encode()
    resp = await post(c)
    assert resp.status_code == 503
    assert code(resp) == "stt_unavailable"
    assert why in resp.json()["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 411, 413])
async def test_daemon_4xx_is_400_invalid_audio(gw, daemon, status):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.status, daemon.body = status, json.dumps({"error": "nope"}).encode()
    resp = await post(c)
    assert resp.status_code == 400
    assert code(resp) == "invalid_audio"


@pytest.mark.asyncio
async def test_daemon_500_is_502(gw, daemon):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.status, daemon.body = 500, json.dumps({"error": "decode failed"}).encode()
    resp = await post(c)
    assert resp.status_code == 502
    assert code(resp) == "upstream_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"<html>oops</html>", b"[]", b"{}", b'{"text": 5}', b'{"text": null}'])
async def test_daemon_answer_without_text_is_502(gw, daemon, body):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.body = body
    resp = await post(c)
    assert resp.status_code == 502
    assert code(resp) == "upstream_error"


# --- transcribe_pcm on raw PCM (the device route's entry) -------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("pcm,status", [
    (b"", 400),
    (b"\x00" * 101, 400),
    (b"\x00" * (stt.MAX_PCM_BYTES + 2), 413),
])
async def test_transcribe_pcm_rejects_bad_pcm_without_contacting_the_daemon(daemon, pcm, status):
    with pytest.raises(GatewayError) as exc:
        await stt.transcribe_pcm(pcm, {"port": daemon.port, "model": MODEL})
    assert exc.value.status == status
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_the_daemon_is_always_reached_on_loopback_whatever_the_manifest_says(monkeypatch):
    seen = []

    class Recorder(httpx.AsyncClient):
        async def post(self, url, **kw):
            seen.append(url)
            return httpx.Response(200, json={"text": "x"})

    monkeypatch.setattr(stt.httpx, "AsyncClient", Recorder)
    manifest = {"port": 6975, "model": MODEL, "host": "10.9.8.7", "url": "http://evil.test:1/stt"}
    assert await stt.transcribe_pcm(b"\x00\x00", manifest) == "x"
    assert seen == ["http://127.0.0.1:6975/stt"]


@pytest.mark.asyncio
async def test_a_disconnected_client_never_reaches_the_daemon(gw, daemon, monkeypatch):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    from starlette.requests import Request

    async def gone(self):
        return True

    monkeypatch.setattr(Request, "is_disconnected", gone)
    resp = await post(c)
    assert resp.status_code == 499
    assert daemon.requests == []


# --- happy paths ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [stt.STT_ALIAS, MODEL])
async def test_transcribes_by_alias_and_by_manifest_model_name(gw, daemon, model):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    pcm = bytes(range(256)) * 20
    resp = await post(c, wav=make_wav(pcm), model=model, extra=[("language", b"en", None)])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"text": "hello world"}
    assert len(daemon.requests) == 1
    got = daemon.requests[0]
    assert got["path"] == "/stt"
    assert got["body"] == pcm  # the frames only: the WAV header is stripped
    assert got["headers"]["Content-Length"] == str(len(pcm))
    assert got["headers"]["X-Sample-Rate"] == "16000"


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["Multipart/Form-Data", "MULTIPART/FORM-DATA"])
async def test_the_media_type_is_case_insensitive(gw, daemon, mime):
    """RFC 9110: media types are case-insensitive, and python-multipart's
    parse_options_header hands the type back as sent."""
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    body, ctype = multipart([("file", make_wav(), "a.wav"), ("model", MODEL.encode(), None)])
    ctype = ctype.replace("multipart/form-data", mime)
    resp = await c.post(URL, content=body, headers={"content-type": ctype})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"text": "hello world"}


@pytest.mark.asyncio
async def test_response_format_text_is_plain_text(gw, daemon):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, extra=[("response_format", b"text", None)])
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.text == "hello world"


@pytest.mark.asyncio
async def test_a_thirty_second_clip_is_accepted(gw, daemon):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    pcm = b"\x01\x00" * (stt.MAX_PCM_BYTES // 2)
    resp = await post(c, wav=make_wav(pcm))
    assert resp.status_code == 200, resp.text
    assert len(daemon.requests[0]["body"]) == stt.MAX_PCM_BYTES


@pytest.mark.asyncio
async def test_neither_audio_nor_transcript_is_logged(gw, daemon, caplog):
    c, data_dir, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.body = json.dumps({"text": "SECRET-TRANSCRIPT-9f2"}).encode()
    with caplog.at_level(0):
        resp = await post(c)
    assert resp.status_code == 200
    assert "SECRET-TRANSCRIPT-9f2" not in caplog.text
