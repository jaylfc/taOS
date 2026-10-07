"""Local text-to-speech through the gateway: ``POST /api/llm/v1/audio/speech``.

The real controller app (``create_app`` on a tmp data dir) with a gateway key
as the credential; the daemon is a real throwaway HTTP server on an ephemeral
127.0.0.1 port that streams chunked PCM, so the bytes that reach it and the
bytes that come back are the ones asserted on. The malformed and failing cases
come first, the happy paths last.
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.llm_gateway import tts
from tinyagentos.llm_gateway.auth import mint_gateway_key

URL = "/api/llm/v1/audio/speech"
MODEL = "vits-piper-en_GB-cori-high"
NATIVE = 22050


# --- fixtures ---------------------------------------------------------------


class Daemon:
    """A stand-in taos-ttsd: chunked raw PCM, records every request it gets."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.error_body = json.dumps({"error": "model loading"}).encode()
        self.pcm_chunks: list[bytes] = [b"\x01\x00" * 500]
        self.sample_rate = str(NATIVE)
        self.channels = "1"
        self.endless = False
        self.closed = threading.Event()  # the client hung up on us mid-stream
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                daemon.requests.append({"path": self.path, "headers": dict(self.headers),
                                        "body": self.rfile.read(length)})
                if daemon.status != 200:
                    self.send_response(daemon.status)
                    self.send_header("Content-Length", str(len(daemon.error_body)))
                    self.end_headers()
                    self.wfile.write(daemon.error_body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "audio/pcm")
                if daemon.sample_rate is not None:
                    self.send_header("X-Sample-Rate", daemon.sample_rate)
                if daemon.channels is not None:
                    self.send_header("X-Channels", daemon.channels)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    if daemon.endless:
                        block = b"\x02\x00" * 2000
                        while True:
                            self._chunk(block)
                            time.sleep(0.01)
                    for chunk in daemon.pcm_chunks:
                        self._chunk(chunk)
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    daemon.closed.set()

            def _chunk(self, data):
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
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


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def write_manifest(data_dir, port, **extra):
    path = data_dir / "voice" / "tts" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"engine": "sherpa-onnx", "model": MODEL, "voice": "cori", "sid": 0,
                "sample_rate": NATIVE, "port": port}
    manifest.update(extra)
    path.write_text(json.dumps(manifest))
    return path


@pytest.fixture(autouse=True)
def _no_manifest_override(monkeypatch):
    monkeypatch.delenv("TAOS_TTS_MANIFEST", raising=False)


@pytest_asyncio.fixture
async def gw(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    key = mint_gateway_key(bound_to="tts-agent", kind="agent",
                           allowed_models=[tts.TTS_ALIAS, MODEL], data_dir=tmp_data_dir)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {key}"}) as c:
        yield c, tmp_data_dir, app, key


async def post(c, **overrides):
    body = {"model": MODEL, "input": "hello there"}
    body.update(overrides)
    for k in [k for k, v in body.items() if v is ...]:
        del body[k]
    return await c.post(URL, json=body)


def code(resp) -> str:
    return resp.json()["error"]["code"]


def pcm_of(samples) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def samples_of(pcm: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm))


def sine(freq, rate, seconds, amp=10000):
    return [round(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(int(rate * seconds))]


def rms(xs):
    return math.sqrt(sum(x * x for x in xs) / len(xs))


def crossings(xs):
    return sum(1 for a, b in zip(xs, xs[1:]) if (a < 0) != (b < 0))


# --- request shape ----------------------------------------------------------


@pytest.mark.asyncio
async def test_no_credentials_is_401(gw, daemon):
    _, data_dir, app, _ = gw
    write_manifest(data_dir, daemon.port)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.post(URL, json={"model": MODEL, "input": "hi"})
    assert resp.status_code == 401
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_non_json_body_is_400(gw):
    c, _, _, _ = gw
    resp = await c.post(URL, content=b"not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert code(resp) == "invalid_request"


@pytest.mark.asyncio
async def test_json_array_body_is_400(gw):
    c, _, _, _ = gw
    resp = await c.post(URL, json=[1, 2])
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_missing_model_is_400(gw):
    c, _, _, _ = gw
    resp = await post(c, model=...)
    assert resp.status_code == 400
    assert "'model'" in resp.json()["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [..., "", "   ", None, 5, ["hi"]])
async def test_missing_empty_or_non_string_input_is_400(gw, daemon, value):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, input=value)
    assert resp.status_code == 400
    assert "'input'" in resp.json()["error"]["message"]
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_oversize_input_is_413(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, input="a" * (tts.MAX_INPUT_CHARS + 1))
    assert resp.status_code == 413
    assert code(resp) == "input_too_long"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_oversize_request_body_is_413(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await c.post(URL, content=b'{"input": "' + b"a" * 300000 + b'"}',
                        headers={"content-type": "application/json"})
    assert resp.status_code == 413
    assert code(resp) == "request_too_large"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_input_at_the_limit_is_accepted(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, input="a" * tts.MAX_INPUT_CHARS)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", ["alloy", "Cori", "", 7, None, ["cori"]])
async def test_voice_other_than_cori_is_400(gw, daemon, voice):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, voice=voice)
    assert resp.status_code == 400
    assert "voice" in resp.json()["error"]["message"]
    assert daemon.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["mp3", "wav", "opus", "flac", "PCM", "", 1, None])
async def test_response_format_other_than_pcm_is_400(gw, daemon, fmt):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, response_format=fmt)
    assert resp.status_code == 400
    assert "response_format" in resp.json()["error"]["message"]
    assert daemon.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [8000, 44100, 24000, 0, -1, -16000, "16000", "22050", True, False,
                                  16000.0, 16000.5, None, [16000], {"a": 1}, 10**30])
async def test_unsupported_sample_rate_is_400_never_the_native_rate(gw, daemon, rate):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, sample_rate=rate)
    assert resp.status_code == 400, resp.text
    assert code(resp) == "invalid_request"
    assert "sample_rate" in resp.json()["error"]["message"]
    assert daemon.requests == []


# --- model names and permission --------------------------------------------


@pytest.mark.asyncio
async def test_unknown_model_is_404(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    key = mint_gateway_key(bound_to="wide", kind="agent", allowed_models=["tts-1"],
                           data_dir=data_dir)
    resp = await c.post(URL, json={"model": "tts-1", "input": "hi"},
                        headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    assert code(resp) == "model_not_found"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_model_the_key_may_not_use_is_403(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    key = mint_gateway_key(bound_to="chat-only", kind="agent", allowed_models=["qwen3-8b"],
                           data_dir=data_dir)
    resp = await c.post(URL, json={"model": tts.TTS_ALIAS, "input": "hi"},
                        headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 403
    assert code(resp) == "model_not_permitted"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_a_stt_only_key_may_not_speak(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    key = mint_gateway_key(bound_to="stt-only", kind="agent", allowed_models=["taos-stt-default"],
                           data_dir=data_dir)
    resp = await c.post(URL, json={"model": tts.TTS_ALIAS, "input": "hi"},
                        headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 403
    assert daemon.requests == []


# --- manifest ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_manifest_is_409_tts_not_installed(gw):
    c, _, _, _ = gw
    resp = await post(c, model=tts.TTS_ALIAS)
    assert resp.status_code == 409
    assert code(resp) == "tts_not_installed"


@pytest.mark.asyncio
async def test_no_manifest_and_unknown_model_is_404_not_409(gw):
    c, data_dir, _, _ = gw
    key = mint_gateway_key(bound_to="wide", kind="agent", allowed_models=["tts-1"],
                           data_dir=data_dir)
    resp = await c.post(URL, json={"model": "tts-1", "input": "hi"},
                        headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "{not json", "[1, 2]", "null", "",
    json.dumps({"model": MODEL, "sample_rate": 22050}),
    json.dumps({"model": MODEL, "sample_rate": 22050, "port": "6976"}),
    json.dumps({"model": MODEL, "sample_rate": 22050, "port": 0}),
    json.dumps({"model": MODEL, "sample_rate": 22050, "port": 65536}),
    json.dumps({"model": MODEL, "sample_rate": 22050, "port": True}),
    json.dumps({"sample_rate": 22050, "port": 6976}),
    json.dumps({"model": 7, "sample_rate": 22050, "port": 6976}),
    json.dumps({"model": MODEL, "port": 6976}),
    json.dumps({"model": MODEL, "sample_rate": "22050", "port": 6976}),
    json.dumps({"model": MODEL, "sample_rate": True, "port": 6976}),
    json.dumps({"model": MODEL, "sample_rate": 0, "port": 6976}),
    json.dumps({"model": MODEL, "sample_rate": -22050, "port": 6976}),
    json.dumps({"model": MODEL, "sample_rate": 22050.0, "port": 6976}),
])
async def test_corrupt_manifest_is_503_not_409(gw, content):
    c, data_dir, _, _ = gw
    path = data_dir / "voice" / "tts" / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(content)
    resp = await post(c, model=tts.TTS_ALIAS)
    assert resp.status_code == 503
    assert code(resp) == "tts_unavailable"


@pytest.mark.asyncio
async def test_manifest_env_override_changes_the_file_read(gw, daemon, monkeypatch, tmp_path):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, closed_port())  # the default path points at a dead port
    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps({"model": MODEL, "sample_rate": NATIVE, "port": daemon.port}))
    monkeypatch.setenv("TAOS_TTS_MANIFEST", str(other))
    assert tts.manifest_path(data_dir) == other
    resp = await post(c)
    assert resp.status_code == 200, resp.text
    assert len(daemon.requests) == 1


def test_default_manifest_path_is_under_the_data_dir(tmp_path):
    assert tts.manifest_path(tmp_path) == tmp_path / "voice" / "tts" / "manifest.json"


@pytest.mark.asyncio
async def test_manifest_host_and_url_keys_are_ignored(gw, daemon):
    """SSRF: the daemon is on 127.0.0.1 whatever the manifest says."""
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port, host="10.9.8.7", url="http://evil.test:1/tts",
                   base_url="http://evil.test:1")
    resp = await post(c)
    assert resp.status_code == 200, resp.text
    assert len(daemon.requests) == 1


@pytest.mark.asyncio
async def test_the_daemon_is_always_reached_on_loopback_whatever_the_manifest_says(monkeypatch):
    seen = []

    class Recorder(httpx.AsyncClient):
        def build_request(self, method, url, **kw):
            seen.append(str(url))
            return super().build_request(method, url, **kw)

        async def send(self, request, **kw):
            return httpx.Response(200, headers={"X-Sample-Rate": "22050", "X-Channels": "1"},
                                  content=b"\x00\x00", request=request)

    monkeypatch.setattr(tts.httpx, "AsyncClient", Recorder)
    manifest = {"port": 6976, "model": MODEL, "sample_rate": NATIVE,
                "host": "10.9.8.7", "url": "http://evil.test:1/tts"}
    up = await tts.open_speech("hi", manifest)
    await up.aclose()
    assert seen == ["http://127.0.0.1:6976/tts"]


# --- the daemon -------------------------------------------------------------


@pytest.mark.asyncio
async def test_daemon_down_on_a_closed_port_is_503(gw):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, closed_port())
    resp = await post(c)
    assert resp.status_code == 503
    assert code(resp) == "tts_unavailable"


@pytest.mark.asyncio
async def test_daemon_503_loading_is_503_and_keeps_the_reason(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.status = 503
    resp = await post(c)
    assert resp.status_code == 503
    assert code(resp) == "tts_unavailable"
    assert "model loading" in resp.json()["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 413])
async def test_daemon_4xx_is_400(gw, daemon, status):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.status = status
    resp = await post(c)
    assert resp.status_code == 400
    assert code(resp) == "invalid_request"


@pytest.mark.asyncio
async def test_daemon_500_is_502(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.status = 500
    resp = await post(c)
    assert resp.status_code == 502
    assert code(resp) == "upstream_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", ["16000", "44100", "abc", "", None])
async def test_daemon_sample_rate_not_the_manifests_is_502(gw, daemon, rate):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.sample_rate = rate
    for extra in ({}, {"sample_rate": 16000}, {"sample_rate": NATIVE}):
        resp = await post(c, **extra)
        assert resp.status_code == 502, (extra, resp.text)
        assert code(resp) == "upstream_error"
        assert "sample rate" in resp.json()["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("channels", ["2", "", None, "x"])
async def test_daemon_channels_not_mono_is_502(gw, daemon, channels):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.channels = channels
    resp = await post(c)
    assert resp.status_code == 502
    assert code(resp) == "upstream_error"


@pytest.mark.asyncio
async def test_a_disconnected_client_never_reaches_the_daemon(gw, daemon, monkeypatch):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    from starlette.requests import Request

    async def gone(self):
        return True

    monkeypatch.setattr(Request, "is_disconnected", gone)
    resp = await post(c)
    assert resp.status_code == 499
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_a_client_gone_mid_upload_is_499_and_never_reaches_the_daemon(gw, daemon, monkeypatch):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    from starlette.requests import ClientDisconnect, Request

    async def cut(self):
        yield b"x"
        raise ClientDisconnect()

    monkeypatch.setattr(Request, "stream", cut)
    resp = await post(c)
    assert resp.status_code == 499
    assert daemon.requests == []


# --- the resampler ----------------------------------------------------------


def run_chunked(pcm: bytes, sizes, src=NATIVE, dst=16000) -> bytes:
    rs = tts.PcmResampler(src, dst)
    out, pos, i = [], 0, 0
    while pos < len(pcm):
        n = sizes[i % len(sizes)]
        out.append(rs.feed(pcm[pos:pos + n]))
        pos += n
        i += 1
    out.append(rs.finish())
    return b"".join(out)


def test_resampler_chunking_is_invisible():
    rng = random.Random(7)
    samples = [round(8000 * math.sin(i * 0.05) + rng.randint(-3000, 3000)) for i in range(9000)]
    pcm = pcm_of(samples)
    whole = run_chunked(pcm, [len(pcm)])
    assert len(whole) // 2 in range(round(9000 * 16000 / NATIVE) - 2, round(9000 * 16000 / NATIVE) + 3)
    for sizes in ([1], [3], [2], [7], [4097], [1, 2, 3, 5, 8, 13, 21], [441 * 2 + 1], [999, 1, 1000, 3]):
        got = run_chunked(pcm, sizes)
        assert len(got) == len(whole), sizes
        diff = max(abs(a - b) for a, b in zip(samples_of(got), samples_of(whole)))
        assert diff <= 1, (sizes, diff)


def test_resampler_odd_byte_counts_do_not_shift_samples():
    pcm = pcm_of(sine(1000, NATIVE, 0.2))
    assert run_chunked(pcm, [1]) == run_chunked(pcm, [len(pcm)])


def test_resampler_trailing_odd_byte_is_dropped_not_fatal():
    pcm = pcm_of(sine(1000, NATIVE, 0.1)) + b"\x7f"
    assert len(run_chunked(pcm, [len(pcm)])) % 2 == 0


def test_resampler_empty_stream_is_empty():
    rs = tts.PcmResampler(NATIVE, 16000)
    assert rs.feed(b"") == b"" and rs.finish() == b""


def test_one_khz_sine_stays_one_khz_at_16k():
    out = samples_of(run_chunked(pcm_of(sine(1000, NATIVE, 1.0)), [4410]))
    assert abs(len(out) - 16000) <= 2
    body = out[200:-200]
    seconds = len(body) / 16000
    assert abs(crossings(body) / seconds / 2 - 1000) <= 10
    assert 0.97 < rms(body) / (10000 / math.sqrt(2)) < 1.03  # passband gain ~ unity


def test_ten_khz_tone_is_attenuated_not_aliased():
    out = samples_of(run_chunked(pcm_of(sine(10000, NATIVE, 1.0)), [4410]))
    body = out[200:-200]
    assert rms(body) / (10000 / math.sqrt(2)) < 0.01  # at least 40 dB down


def test_a_7khz_tone_still_passes():
    out = samples_of(run_chunked(pcm_of(sine(7000, NATIVE, 0.5)), [4410]))
    body = out[200:-200]
    assert rms(body) / (10000 / math.sqrt(2)) > 0.7


def test_resampler_never_overflows_int16():
    pcm = pcm_of([32767, -32768] * 4000)  # worst case: Nyquist square
    for s in samples_of(run_chunked(pcm, [999])):
        assert -32768 <= s <= 32767
    pcm = pcm_of(([32767] * 50 + [-32768] * 50) * 80)
    for s in samples_of(run_chunked(pcm, [999])):
        assert -32768 <= s <= 32767


def test_resampler_speed_ten_seconds_is_well_under_realtime(capsys):
    pcm = pcm_of(sine(1000, NATIVE, 10.0))
    start = time.perf_counter()
    out = run_chunked(pcm, [8192])
    took = time.perf_counter() - start
    print(f"resampled 10 s of audio in {took:.3f} s")
    assert len(out) // 2 > 159990
    assert took < 3.0  # generous; the real number is printed for the PR


# --- happy paths ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [tts.TTS_ALIAS, MODEL])
async def test_native_rate_is_byte_identical_by_alias_and_by_manifest_model(gw, daemon, model):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.pcm_chunks = [bytes(range(256)) * 3, b"\x05", b"\x06" + bytes(range(200, 255)) * 5]
    expected = b"".join(daemon.pcm_chunks)
    resp = await post(c, model=model, input="hello there", voice="cori", response_format="pcm",
                      speed=1.0)
    assert resp.status_code == 200, resp.text
    assert resp.content == expected  # odd chunk splits in flight do not matter
    assert resp.headers["content-type"] == "audio/pcm"
    assert resp.headers["x-sample-rate"] == "22050"
    assert resp.headers["x-channels"] == "1"
    assert len(daemon.requests) == 1
    got = daemon.requests[0]
    assert got["path"] == "/tts"
    assert json.loads(got["body"]) == {"text": "hello there"}


@pytest.mark.asyncio
async def test_explicit_native_rate_passes_through_byte_identical(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.pcm_chunks = [bytes(range(256)) * 9]
    resp = await post(c, sample_rate=NATIVE)
    assert resp.status_code == 200
    assert resp.content == daemon.pcm_chunks[0]
    assert resp.headers["x-sample-rate"] == "22050"


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [4411, 1, 100000])  # odd, one byte, one slab (sliced in flight)
async def test_sixteen_khz_is_resampled_and_labelled(gw, daemon, chunk):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    pcm = pcm_of(sine(1000, NATIVE, 1.0 if chunk != 1 else 0.05))
    daemon.pcm_chunks = [pcm[i:i + chunk] for i in range(0, len(pcm), chunk)]
    resp = await post(c, sample_rate=16000)
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-sample-rate"] == "16000"
    assert resp.headers["x-channels"] == "1"
    assert resp.headers["content-type"] == "audio/pcm"
    out = samples_of(resp.content)
    assert abs(len(out) - round(len(pcm) // 2 * 16000 / NATIVE)) <= 2
    body = out[100:-100]
    assert abs(crossings(body) / (len(body) / 16000) / 2 - 1000) <= 20
    assert resp.content == run_chunked(pcm, [len(pcm)])  # the route is the resampler, nothing else


@pytest.mark.asyncio
async def test_the_input_text_is_never_logged(gw, daemon, caplog):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    with caplog.at_level(0):
        resp = await post(c, input="SECRET-UTTERANCE-9f2")
        bad = await post(c, input="SECRET-UTTERANCE-9f2", voice="nope")
    assert resp.status_code == 200 and bad.status_code == 400
    assert "SECRET-UTTERANCE-9f2" not in caplog.text
    assert "SECRET-UTTERANCE-9f2" not in json.dumps(bad.json())


# --- client disconnect -----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_client_disconnect_mid_stream_closes_the_upstream(gw, daemon, spec_version, monkeypatch):
    """Both ways a server reports a vanished client: ASGI <= 2.3 sends
    ``http.disconnect`` on receive, 2.4 makes ``send`` raise OSError."""
    _, data_dir, app, key = gw
    write_manifest(data_dir, daemon.port)
    daemon.endless = True
    closes = []
    real_aclose = tts.Speech.aclose

    async def spy(self):
        closes.append(1)
        await real_aclose(self)

    monkeypatch.setattr(tts.Speech, "aclose", spy)
    body = json.dumps({"model": MODEL, "input": "go on and on"}).encode()
    gone = asyncio.Event()
    sent_body = []
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if gone.is_set() and spec_version == "2.4":
            raise OSError("client went away")
        if message["type"] == "http.response.body" and message.get("body"):
            sent_body.append(len(message["body"]))
            if len(sent_body) >= 3:
                gone.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": spec_version},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": URL,
             "raw_path": URL.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"host", b"test"), (b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode()),
                         (b"authorization", f"Bearer {key}".encode())],
             "client": ("127.0.0.1", 5), "server": ("test", 80)}
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=10)
    except OSError:
        pass  # what a server swallows when its client is gone
    assert 3 <= len(sent_body) < 100  # it stopped; the daemon would have gone on forever
    assert closes == [1], "the route must close the upstream stream itself, once"
    assert await asyncio.to_thread(daemon.closed.wait, 5), "upstream connection was leaked"


@pytest.mark.asyncio
async def test_disconnect_before_the_first_chunk_still_closes_the_upstream(gw, daemon, monkeypatch):
    """ASGI <= 2.3: StreamingResponse can be cancelled before it ever iterates
    the body generator, so the generator's own ``finally`` never runs. The
    route's response is driven directly: through the app, the startup-guard
    middleware pulls the body itself and hides the race."""
    from starlette.requests import Request

    from tinyagentos.llm_gateway import router as gw_router

    _, data_dir, app, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.endless = True
    closes = []
    real_aclose = tts.Speech.aclose

    async def spy(self):
        closes.append(1)
        await real_aclose(self)

    async def alive(self):
        return False

    class Caller:
        def may_use(self, name):
            return True

    monkeypatch.setattr(tts.Speech, "aclose", spy)
    monkeypatch.setattr(Request, "is_disconnected", alive)
    body = json.dumps({"model": MODEL, "input": "never heard"}).encode()

    async def request_receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": URL,
             "raw_path": URL.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"content-type", b"application/json")], "app": app}
    response = await gw_router.audio_speech(Request(scope, request_receive), Caller())

    async def receive():
        return {"type": "http.disconnect"}  # gone before the first chunk is pulled

    async def send(message):
        # A real server's send takes a moment on the headers; the disconnect
        # lands in that gap, before the body generator is first iterated.
        await asyncio.sleep(0.05)

    await asyncio.wait_for(response(scope, receive, send), timeout=10)
    assert closes, "the route must close the upstream even if the body never started"
    assert await asyncio.to_thread(daemon.closed.wait, 5), "upstream connection was leaked"


# --- the daemon failing mid-stream ------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ReadTimeout("SECRET-UTTERANCE-9f2"),
                                 httpx.RemoteProtocolError("SECRET-UTTERANCE-9f2")])
@pytest.mark.parametrize("rate", [None, 16000])
async def test_daemon_failing_mid_stream_ends_the_audio_cleanly(gw, daemon, monkeypatch, caplog,
                                                                exc, rate):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    first = pcm_of(sine(1000, NATIVE, 0.2))
    closes = []
    real_aclose = tts.Speech.aclose

    async def spy(self):
        closes.append(1)
        await real_aclose(self)

    async def broken(self):
        yield first
        raise exc

    monkeypatch.setattr(tts.Speech, "aclose", spy)
    monkeypatch.setattr(tts.Speech, "chunks", broken)
    extra = {} if rate is None else {"sample_rate": rate}
    with caplog.at_level(0):
        resp = await post(c, **extra)
    assert resp.status_code == 200
    assert resp.content  # the audio before the failure arrived
    if rate is None:
        assert resp.content == first
    assert closes == [1]
    assert "SECRET-UTTERANCE-9f2" not in caplog.text


# --- OpenAI streaming: stream_format=sse ------------------------------------


def sse_events(text: str) -> list[dict]:
    """The JSON payloads of an SSE body; every frame must be exactly ``data: <json>``."""
    assert text.endswith("\n\n")
    frames = text[:-2].split("\n\n")
    for f in frames:
        assert f.startswith("data: ") and "\n" not in f, f
    return [json.loads(f[len("data: "):]) for f in frames]


def decode_audio(events) -> bytes:
    import base64
    return b"".join(base64.b64decode(e["audio"], validate=True)
                    for e in events if e["type"] == "speech.audio.delta")


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [None, NATIVE, 16000])
async def test_sse_events_decode_to_exactly_the_audio_format_bytes(gw, daemon, rate):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    pcm = pcm_of(sine(1000, NATIVE, 0.3))
    daemon.pcm_chunks = [pcm[i:i + 3001] for i in range(0, len(pcm), 3001)]
    extra = {} if rate is None else {"sample_rate": rate}
    plain = await post(c, **extra)
    resp = await post(c, stream_format="sse", **extra)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-sample-rate"] == plain.headers["x-sample-rate"]
    assert resp.headers["x-channels"] == "1"
    events = sse_events(resp.text)
    assert all(e["type"] == "speech.audio.delta" for e in events[:-1])
    assert len(events) > 2  # one delta per chunk, not one lump
    assert events[-1] == {"type": "speech.audio.done",
                          "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
    assert decode_audio(events) == plain.content


@pytest.mark.asyncio
async def test_stream_format_audio_is_the_unchanged_default(gw, daemon):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.pcm_chunks = [bytes(range(256)) * 3]
    for extra in ({}, {"stream_format": "audio"}):
        resp = await post(c, **extra)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/pcm"
        assert resp.content == daemon.pcm_chunks[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["json", "SSE", "", None, 1, True, ["sse"]])
async def test_invalid_stream_format_is_400(gw, daemon, value):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, stream_format=value)
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/json")
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_sse_errors_before_streaming_are_json(gw, daemon):
    c, data_dir, _, _ = gw
    no_manifest = await post(c, model=tts.TTS_ALIAS, stream_format="sse")
    assert no_manifest.status_code == 409 and code(no_manifest) == "tts_not_installed"
    write_manifest(data_dir, daemon.port)
    daemon.status = 503
    down = await post(c, stream_format="sse")
    assert down.status_code == 503
    for r in (no_manifest, down):
        assert r.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_sse_text_is_never_logged(gw, daemon, caplog):
    c, data_dir, _ = gw[0], gw[1], None
    write_manifest(data_dir, daemon.port)
    with caplog.at_level(0):
        resp = await post(c, input="SECRET-UTTERANCE-9f2", stream_format="sse")
    assert resp.status_code == 200
    assert "SECRET-UTTERANCE-9f2" not in caplog.text
    assert "SECRET-UTTERANCE-9f2" not in resp.text


@pytest.mark.asyncio
async def test_sse_parses_with_the_openai_schema(gw, daemon):
    """openai-python has no speech event types yet; validate against the
    published schema's required fields (SpeechAudioDeltaEvent / DoneEvent)."""
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    resp = await post(c, stream_format="sse")
    events = sse_events(resp.text)
    for e in events[:-1]:
        assert set(e) == {"type", "audio"} and isinstance(e["audio"], str)
    done = events[-1]
    assert set(done) == {"type", "usage"}
    assert set(done["usage"]) == {"input_tokens", "output_tokens", "total_tokens"}
    assert all(isinstance(v, int) for v in done["usage"].values())


@pytest.mark.asyncio
async def test_sse_disconnect_before_the_first_chunk_still_closes_the_upstream(gw, daemon, monkeypatch):
    from starlette.requests import Request

    from tinyagentos.llm_gateway import router as gw_router

    _, data_dir, app, _ = gw
    write_manifest(data_dir, daemon.port)
    daemon.endless = True
    closes = []
    real_aclose = tts.Speech.aclose

    async def spy(self):
        closes.append(1)
        await real_aclose(self)

    async def alive(self):
        return False

    class Caller:
        def may_use(self, name):
            return True

    monkeypatch.setattr(tts.Speech, "aclose", spy)
    monkeypatch.setattr(Request, "is_disconnected", alive)
    body = json.dumps({"model": MODEL, "input": "never heard", "stream_format": "sse"}).encode()

    async def request_receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": URL,
             "raw_path": URL.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"content-type", b"application/json")], "app": app}
    response = await gw_router.audio_speech(Request(scope, request_receive), Caller())

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        await asyncio.sleep(0.05)

    await asyncio.wait_for(response(scope, receive, send), timeout=10)
    assert closes, "the route must close the upstream even if the SSE body never started"
    assert await asyncio.to_thread(daemon.closed.wait, 5), "upstream connection was leaked"


@pytest.mark.asyncio
async def test_sse_client_disconnect_mid_stream_closes_the_upstream(gw, daemon, monkeypatch):
    from tinyagentos.llm_gateway import router as gw_router  # noqa: F401

    _, data_dir, app, key = gw
    write_manifest(data_dir, daemon.port)
    daemon.endless = True
    closes = []
    real_aclose = tts.Speech.aclose

    async def spy(self):
        closes.append(1)
        await real_aclose(self)

    monkeypatch.setattr(tts.Speech, "aclose", spy)
    body = json.dumps({"model": MODEL, "input": "x", "stream_format": "sse"}).encode()
    sent_body = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        while len(sent_body) < 3:
            await asyncio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            sent_body.append(message.get("body", b""))
            if len(sent_body) > 2:
                raise OSError("client gone")

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": URL,
             "raw_path": URL.encode(), "query_string": b"", "root_path": "", "app": app,
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode()),
                         (b"authorization", f"Bearer {key}".encode())],
             "client": ("127.0.0.1", 5), "server": ("test", 80)}
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=10)
    except OSError:
        pass
    assert 3 <= len(sent_body) < 100
    assert closes == [1]
    assert await asyncio.to_thread(daemon.closed.wait, 5), "upstream connection was leaked"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ReadTimeout("SECRET-UTTERANCE-9f2"),
                                 httpx.RemoteProtocolError("SECRET-UTTERANCE-9f2")])
@pytest.mark.parametrize("rate", [None, 16000])
async def test_sse_daemon_failing_mid_stream_closes_upstream_and_ends_without_done(
        gw, daemon, monkeypatch, caplog, exc, rate):
    c, data_dir, _, _ = gw
    write_manifest(data_dir, daemon.port)
    first = pcm_of(sine(1000, NATIVE, 0.2))
    closes = []
    real_aclose = tts.Speech.aclose

    async def spy(self):
        closes.append(1)
        await real_aclose(self)

    async def broken(self):
        yield first
        raise exc

    monkeypatch.setattr(tts.Speech, "aclose", spy)
    monkeypatch.setattr(tts.Speech, "chunks", broken)
    extra = {} if rate is None else {"sample_rate": rate}
    with caplog.at_level(0):
        resp = await post(c, stream_format="sse", **extra)
    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert events and all(e["type"] == "speech.audio.delta" for e in events)  # no done: truncated
    if rate is None:
        assert decode_audio(events) == first
    assert closes == [1]
    assert "SECRET-UTTERANCE-9f2" not in caplog.text
