"""S6 / S6b: the device voice routes.

``POST /api/device/v1/voice/tts`` (scope ``voice:tts``) and
``POST /api/device/v1/voice`` (scope ``voice:stt``), answered in-process by the
gateway's ``tts`` / ``stt`` helpers. The app is the real ``create_app`` on a tmp
data dir; the daemons are real throwaway 127.0.0.1 HTTP servers. Failures
first, happy paths last.
"""
from __future__ import annotations

import json
import logging
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.llm_gateway import stt, tts

TTS_URL = "/api/device/v1/voice/tts"
STT_URL = "/api/device/v1/voice"
PLAIN = "http://testserver:6969"
TLS = "https://testserver:6974"
NATIVE = 22050
SECRET = "SECRET-MARKER-9f3a"


class Daemon:
    """A stand-in taos-ttsd (/tts, chunked PCM) and taos-sttd (/stt)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.pcm = b"\x01\x00" * 2205  # 0.1 s at the native rate
        self.text = "hello world"
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                daemon.requests.append({"path": self.path, "body": self.rfile.read(n)})
                if self.path == "/stt":
                    body = json.dumps({"text": daemon.text}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "audio/pcm")
                self.send_header("X-Sample-Rate", str(NATIVE))
                self.send_header("X-Channels", "1")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(f"{len(daemon.pcm):x}\r\n".encode() + daemon.pcm + b"\r\n0\r\n\r\n")
                self.wfile.flush()

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
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


@pytest.fixture(autouse=True)
def _no_manifest_override(monkeypatch):
    monkeypatch.delenv("TAOS_TTS_MANIFEST", raising=False)
    monkeypatch.delenv("TAOS_STT_MANIFEST", raising=False)


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def write_tts_manifest(data_dir, port):
    p = data_dir / "voice" / "tts" / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"engine": "sherpa-onnx", "model": "vits-piper-en_GB-cori-high",
                             "sample_rate": NATIVE, "port": port}))


def write_stt_manifest(data_dir, port):
    p = data_dir / "voice" / "stt" / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"engine": "sherpa-onnx", "model": "parakeet", "port": port}))


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def _client(app, base=PLAIN):
    return AsyncClient(transport=ASGITransport(app=app), base_url=base)


@pytest_asyncio.fixture
async def vapp(app, client):
    return app


async def _device(app, platform="ios", scopes=("voice:tts", "voice:stt")):
    st = app.state.device_store
    d = await st.register(user_id="u1", platform=platform)
    if scopes is not None:
        await st.set_scopes(d["device_id"], list(scopes))
    return d["scoped_token"]


def err(resp):
    return resp.json()["detail"]["error"]


# ------------------------------------------------------------------ auth

@pytest.mark.asyncio
@pytest.mark.parametrize("url", [TTS_URL, STT_URL])
async def test_no_token_is_401(vapp, url):
    async with _client(vapp) as c:
        r = await c.post(url, json={"text": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [TTS_URL, STT_URL])
async def test_bad_token_is_401(vapp, url):
    async with _client(vapp) as c:
        r = await c.post(url, headers=_bearer("taosdev_nope"), json={"text": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [TTS_URL, STT_URL])
async def test_session_user_without_a_device_is_not_accepted(vapp, url):
    """A device API: a session cookie / non-device bearer never reaches the route."""
    async with _client(vapp) as c:
        r = await c.post(url, headers=_bearer("not-a-device-token"), json={"text": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_tts_missing_scope_names_voice_tts(vapp, tmp_data_dir, daemon):
    write_tts_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp, scopes=("voice:stt",))
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi"})
    assert r.status_code == 403
    assert r.json()["detail"] == {"error": "device_scope_missing", "scope": "voice:tts"}
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_stt_missing_scope_names_voice_stt(vapp, tmp_data_dir, daemon):
    write_stt_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp, scopes=("voice:tts",))
    async with _client(vapp) as c:
        r = await c.post(STT_URL, headers=_bearer(tok), content=b"\x00\x00" * 10)
    assert r.status_code == 403
    assert r.json()["detail"] == {"error": "device_scope_missing", "scope": "voice:stt"}
    assert daemon.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["ios", "watchos", "android"])
async def test_legacy_null_scope_token_is_refused_on_both(vapp, tmp_data_dir, daemon, platform):
    write_tts_manifest(tmp_data_dir, daemon.port)
    write_stt_manifest(tmp_data_dir, daemon.port)
    tok = "taosdev_legacy_" + "b" * 20
    db = vapp.state.device_store._db
    await db.execute(
        "INSERT INTO devices (device_id, user_id, platform, push_token, scoped_token) "
        "VALUES (?, 'u1', ?, '', ?)", ("legacy-" + platform, platform, tok))
    await db.commit()
    async with _client(vapp) as c:
        r1 = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi"})
        r2 = await c.post(STT_URL, headers=_bearer(tok), content=b"\x00\x00" * 10)
    assert r1.status_code == r2.status_code == 403
    assert err(r1) == err(r2) == "device_scope_missing"
    assert daemon.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [TTS_URL, STT_URL])
async def test_embedded_off_the_tls_listener_is_refused(vapp, url):
    tok = await _device(vapp, platform="embedded", scopes=("voice:stt", "voice:tts", "chat:send"))
    async with _client(vapp, PLAIN) as c:
        r = await c.post(url, headers=_bearer(tok), json={"text": "hi"})
    assert r.status_code == 403
    assert err(r) == "device_tls_required"


@pytest.mark.asyncio
async def test_embedded_default_scopes_on_tls_lack_voice_then_talk_set_passes(
        vapp, tmp_data_dir, daemon):
    write_tts_manifest(tmp_data_dir, daemon.port)
    st = vapp.state.device_store
    d = await st.register(user_id="u1", platform="embedded")  # glance + answer only
    tok = d["scoped_token"]
    async with _client(vapp, TLS) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi"})
        assert r.status_code == 403
        assert r.json()["detail"] == {"error": "device_scope_missing", "scope": "voice:tts"}
        await st.set_scopes(d["device_id"], ["voice:stt", "voice:tts", "chat:send"])
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi"})
    assert r.status_code == 200, r.text
    assert r.content == daemon.pcm
    assert r.headers["x-sample-rate"] == str(NATIVE)


# ------------------------------------------------------------------ TTS errors

@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"content": b"not json"},
    {"json": [1, 2]},
    {"json": {}},
    {"json": {"text": ""}},
    {"json": {"text": "   "}},
    {"json": {"text": 5}},
    {"json": {"text": ["hi"]}},
    {"json": {"text": "hi", "sample_rate": True}},
    {"json": {"text": "hi", "sample_rate": "16000"}},
    {"json": {"text": "hi", "sample_rate": 16000.0}},
    {"json": {"text": "hi", "sample_rate": None}},
])
async def test_tts_bad_body_is_400(vapp, tmp_data_dir, daemon, kwargs):
    write_tts_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers={**_bearer(tok), "content-type": "application/json"},
                         **kwargs)
    assert r.status_code == 400, r.text
    assert err(r) == "invalid_request"
    assert daemon.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [44100, 8000, 0, -16000])
async def test_tts_bad_rate_is_400_never_native(vapp, tmp_data_dir, daemon, rate):
    write_tts_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi", "sample_rate": rate})
    assert r.status_code == 400, r.text
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_tts_text_too_long_is_413(vapp, tmp_data_dir, daemon):
    write_tts_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok),
                         json={"text": "a" * (tts.MAX_INPUT_CHARS + 1)})
        ok = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "a" * tts.MAX_INPUT_CHARS})
    assert r.status_code == 413
    assert err(r) == "input_too_long"
    assert ok.status_code == 200
    assert len(daemon.requests) == 1


@pytest.mark.asyncio
async def test_tts_oversize_body_is_413_before_parsing(vapp, tmp_data_dir, daemon):
    write_tts_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers={**_bearer(tok), "content-type": "application/json"},
                         content=b'{"text": "' + b"a" * 300000 + b'"}')
    assert r.status_code == 413
    # The device contract has ONE 413 for speech: the gateway's generic
    # request_too_large must not leak through.
    assert err(r) == "input_too_long"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_tts_not_installed_is_409(vapp):
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi"})
    assert r.status_code == 409
    assert err(r) == "tts_not_installed"


@pytest.mark.asyncio
async def test_tts_daemon_down_is_503(vapp, tmp_data_dir):
    write_tts_manifest(tmp_data_dir, closed_port())
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi"})
    assert r.status_code == 503
    assert err(r) == "tts_unavailable"


@pytest.mark.asyncio
async def test_tts_engine_fault_is_502(vapp, tmp_data_dir, daemon):
    write_tts_manifest(tmp_data_dir, daemon.port)
    p = tmp_data_dir / "voice" / "tts" / "manifest.json"
    m = json.loads(p.read_text())
    m["sample_rate"] = 24000  # the daemon says 22050: we do not guess
    p.write_text(json.dumps(m))
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi", "sample_rate": 16000})
    assert r.status_code == 502
    assert err(r) == "upstream_error"


@pytest.mark.asyncio
async def test_tts_disconnect_before_open_is_499_and_daemon_untouched(
        vapp, tmp_data_dir, daemon, monkeypatch):
    write_tts_manifest(tmp_data_dir, daemon.port)
    from starlette.requests import Request

    async def gone(self):
        return True

    monkeypatch.setattr(Request, "is_disconnected", gone)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi"})
    assert r.status_code == 499
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_tts_text_is_never_logged(vapp, tmp_data_dir, daemon, caplog):
    write_tts_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    caplog.set_level(logging.DEBUG)
    async with _client(vapp) as c:
        ok = await c.post(TTS_URL, headers=_bearer(tok), json={"text": SECRET})
        bad = await c.post(TTS_URL, headers=_bearer(tok),
                           json={"text": SECRET, "sample_rate": 44100})
        big = await c.post(TTS_URL, headers=_bearer(tok),
                           json={"text": SECRET * 1000})
    assert (ok.status_code, bad.status_code, big.status_code) == (200, 400, 413)
    assert SECRET not in caplog.text
    assert SECRET not in bad.text and SECRET not in big.text


# ------------------------------------------------------------------ TTS happy

@pytest.mark.asyncio
async def test_tts_native_rate_headers_and_bytes(vapp, tmp_data_dir, daemon):
    write_tts_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hello there"})
        r2 = await c.post(TTS_URL, headers=_bearer(tok),
                          json={"text": "hello there", "sample_rate": NATIVE, "extra": [1]})
    for x in (r, r2):
        assert x.status_code == 200, x.text
        assert x.headers["content-type"] == "audio/pcm"
        assert x.headers["x-sample-rate"] == "22050"
        assert x.headers["x-channels"] == "1"
        assert x.content == daemon.pcm
    assert json.loads(daemon.requests[0]["body"]) == {"text": "hello there"}
    assert daemon.requests[0]["path"] == "/tts"


@pytest.mark.asyncio
async def test_tts_16k_is_resampled_and_consistent(vapp, tmp_data_dir, daemon):
    write_tts_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(TTS_URL, headers=_bearer(tok), json={"text": "hi", "sample_rate": 16000})
    assert r.status_code == 200, r.text
    assert r.headers["x-sample-rate"] == "16000"
    assert r.headers["x-channels"] == "1"
    assert len(r.content) % 2 == 0
    expected = len(daemon.pcm) // 2 * 16000 // NATIVE
    assert abs(len(r.content) // 2 - expected) <= 2
    assert r.content != daemon.pcm


# ------------------------------------------------------------------ STT

def _pcm(n_samples):
    return struct.pack(f"<{n_samples}h", *([7] * n_samples))


@pytest.mark.asyncio
async def test_stt_ok_returns_text_and_forwards_the_pcm(vapp, tmp_data_dir, daemon):
    write_stt_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    pcm = _pcm(1600)
    async with _client(vapp) as c:
        for ct in ("application/octet-stream", "audio/pcm", None):
            h = _bearer(tok) if ct is None else {**_bearer(tok), "content-type": ct}
            r = await c.post(STT_URL, headers=h, content=pcm)
            assert r.status_code == 200, (ct, r.text)
            assert r.json() == {"text": "hello world"}
    assert daemon.requests[0]["body"] == pcm
    assert daemon.requests[0]["path"] == "/stt"


@pytest.mark.asyncio
async def test_stt_at_the_cap_is_accepted_and_one_sample_over_is_413(vapp, tmp_data_dir, daemon):
    write_stt_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        ok = await c.post(STT_URL, headers=_bearer(tok), content=b"\x00\x00" * (stt.MAX_PCM_BYTES // 2))
        big = await c.post(STT_URL, headers=_bearer(tok),
                           content=b"\x00\x00" * (stt.MAX_PCM_BYTES // 2 + 1))
    assert ok.status_code == 200
    assert big.status_code == 413
    assert err(big) == "audio_too_large"
    assert len(daemon.requests) == 1


@pytest.mark.asyncio
async def test_stt_oversize_without_content_length_is_cut_at_the_cap(vapp, tmp_data_dir, daemon):
    write_stt_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)

    async def gen():
        for _ in range(40):
            yield b"\x00\x00" * 20000

    async with _client(vapp) as c:
        r = await c.post(STT_URL, headers=_bearer(tok), content=gen())
    assert r.status_code == 413
    assert daemon.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"", b"\x00", b"\x00\x00\x00"])
async def test_stt_empty_or_odd_is_400(vapp, tmp_data_dir, daemon, body):
    write_stt_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(STT_URL, headers=_bearer(tok), content=body)
    assert r.status_code == 400
    assert err(r) == "invalid_audio"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_stt_wrong_content_type_is_415(vapp, tmp_data_dir, daemon):
    write_stt_manifest(tmp_data_dir, daemon.port)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(STT_URL, headers={**_bearer(tok), "content-type": "application/json"},
                         content=b"\x00\x00" * 10)
    assert r.status_code == 415
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_stt_not_installed_is_409(vapp):
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(STT_URL, headers=_bearer(tok), content=b"\x00\x00" * 10)
    assert r.status_code == 409
    assert err(r) == "stt_not_installed"


@pytest.mark.asyncio
async def test_stt_daemon_down_is_503(vapp, tmp_data_dir):
    write_stt_manifest(tmp_data_dir, closed_port())
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(STT_URL, headers=_bearer(tok), content=b"\x00\x00" * 10)
    assert r.status_code == 503
    assert err(r) == "stt_unavailable"


@pytest.mark.asyncio
async def test_stt_disconnect_before_transcribing_is_499(vapp, tmp_data_dir, daemon, monkeypatch):
    write_stt_manifest(tmp_data_dir, daemon.port)
    from starlette.requests import Request

    async def gone(self):
        return True

    monkeypatch.setattr(Request, "is_disconnected", gone)
    tok = await _device(vapp)
    async with _client(vapp) as c:
        r = await c.post(STT_URL, headers=_bearer(tok), content=b"\x00\x00" * 10)
    assert r.status_code == 499
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_stt_text_is_never_logged_and_audio_is_not_persisted(
        vapp, tmp_data_dir, daemon, caplog):
    write_stt_manifest(tmp_data_dir, daemon.port)
    daemon.text = SECRET
    tok = await _device(vapp)
    audio = b"\x11\x22\x33\x44" * 4000  # distinctive, 16 KB
    before = {p for p in tmp_data_dir.rglob("*") if p.is_file()}
    caplog.set_level(logging.DEBUG)
    async with _client(vapp) as c:
        r = await c.post(STT_URL, headers=_bearer(tok), content=audio)
    assert r.status_code == 200 and r.json() == {"text": SECRET}
    assert SECRET not in caplog.text
    after = {p for p in tmp_data_dir.rglob("*") if p.is_file()}
    assert after == before
    for p in after:
        assert audio[:64] not in p.read_bytes(), p
