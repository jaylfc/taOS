"""Local text-to-speech: the helpers behind ``POST /audio/speech``.

Pure and route-free on purpose, like ``stt``: the gateway route and a later
device voice route both import ``load_manifest`` / ``open_speech`` /
``PcmResampler`` and add only their own transport.

The engine is ``taos-ttsd``, a resident daemon on THIS host. ``POST /tts`` with
``{"text": ...}`` answers chunked raw PCM, s16le mono, ``Content-Type:
audio/pcm``, with ``X-Sample-Rate`` (the voice's native rate, 22050 for Piper
en_GB-cori-high) and ``X-Channels: 1``. Its installer writes
``<data_dir>/voice/tts/manifest.json`` (``TAOS_TTS_MANIFEST`` overrides the
path, read at call time); from it we take the port, the model name and the
native ``sample_rate``, nothing else.

Hard rules, each pinned by a test:

- The host is the literal ``127.0.0.1``. A manifest cannot point the gateway
  at another machine (no ``host`` or ``url`` key is ever read).
- No fallback of any kind. When the daemon is down or loading the caller gets
  a 503; the text is never handed to a cloud backend or a second attempt. This
  module imports nothing from the chat routing.
- The input text is never logged, traced or kept, and this module logs only
  fixed strings.
- Sample rate: the audio is sent at the native rate (omitted or equal
  ``sample_rate``, byte-identical to the daemon's output) or at 16 kHz (the
  Orb's rate, resampled here, streaming). Any other rate is a 400, never a
  silent native-rate answer. The daemon's ``X-Sample-Rate`` must equal the
  manifest's ``sample_rate``, otherwise 502: we do not guess.

Model names: the alias ``taos-tts-default`` or the manifest's own ``model``
value; any other name is a 404 ``model_not_found``. The caller's key must list
the name it asks for (``may_use``).
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import struct
from math import gcd
from operator import mul
from pathlib import Path

import anyio
import anyio.to_thread
import httpx
from starlette.responses import StreamingResponse

from tinyagentos.llm_gateway.errors import GatewayError, upstream_error

logger = logging.getLogger(__name__)

TTS_ALIAS = "taos-tts-default"
TTS_VOICE = "cori"
MANIFEST_ENV = "TAOS_TTS_MANIFEST"
# OpenAI's own limit for /audio/speech; Piper speaks ~15 characters a second.
MAX_INPUT_CHARS = 4096
# The Orb's rate: the one rate besides the native one the route will produce.
RESAMPLED_RATE = 16000
_LOOPBACK = "127.0.0.1"
# Synthesis starts streaming at once, but the first chunk of a long input can
# take a moment on an SBC CPU: allow it, but fail a dead socket fast. Reads
# between chunks use the same bound.
# Bytes of PCM filtered per worker-thread hop (~25 ms of work).
_SLICE = 8192
_TIMEOUT = httpx.Timeout(60.0, connect=2.0)


def _unavailable(message: str) -> GatewayError:
    return GatewayError(503, message, type="api_error", code="tts_unavailable")


def manifest_path(data_dir) -> Path:
    override = os.environ.get(MANIFEST_ENV)
    if override:
        return Path(override)
    return Path(data_dir) / "voice" / "tts" / "manifest.json"


def _strict_int(value) -> bool:
    # bool is an int subclass; True is not a port or a rate.
    return isinstance(value, int) and not isinstance(value, bool)


def load_manifest(data_dir) -> dict:
    """The installer's manifest, validated.

    Missing is 409 ``tts_not_installed`` (a state the user can fix by
    installing). Present but unusable is 503 ``tts_unavailable``: a corrupt
    file must never read as "not installed".
    """
    path = manifest_path(data_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise GatewayError(
            409, "text-to-speech is not installed on this device",
            code="tts_not_installed",
        ) from None
    except OSError:
        raise _unavailable("the text-to-speech manifest cannot be read") from None
    try:
        manifest = json.loads(raw)
    except ValueError:
        raise _unavailable("the text-to-speech manifest is not valid JSON") from None
    if not isinstance(manifest, dict):
        raise _unavailable("the text-to-speech manifest is not a JSON object")
    port = manifest.get("port")
    if not _strict_int(port) or not 1 <= port <= 65535:
        raise _unavailable("the text-to-speech manifest has no valid port")
    model = manifest.get("model")
    if not isinstance(model, str) or not model:
        raise _unavailable("the text-to-speech manifest has no model name")
    rate = manifest.get("sample_rate")
    if not _strict_int(rate) or not 1 <= rate <= 384000:
        raise _unavailable("the text-to-speech manifest has no valid sample_rate")
    return manifest


def check_sample_rate(requested, native: int) -> int:
    """The rate to send: ``native`` when omitted, else ``native`` or 16 kHz, else a 400."""
    if requested is None:
        return native
    if not _strict_int(requested) or requested not in (native, RESAMPLED_RATE):
        raise GatewayError(
            400,
            f"'sample_rate' must be {native} (the voice's native rate), "
            f"{RESAMPLED_RATE}, or omitted",
            code="invalid_request",
        )
    return requested


# --- resampling -------------------------------------------------------------

def _dot(a, b):
    # sum(map(mul)) beats math.sumprod here (~5x): sumprod's extended-precision
    # accumulation is wasted on 48 taps that are rounded to int16 anyway.
    return sum(map(mul, a, b))


def _bessel_i0(x: float) -> float:
    total = term = 1.0
    k = 1
    while term > 1e-12 * total:
        term *= (x / (2 * k)) ** 2
        total += term
        k += 1
    return total


class PcmResampler:
    """Streaming PCM16 mono rational resampler: a windowed-sinc polyphase FIR.

    Pure Python (numpy is not a core dependency). Output sample ``n`` sits at
    input position ``n * src / dst``; each of the ``dst / gcd`` fractional
    phases has its own ``2 * HALF_TAPS``-tap Kaiser-windowed sinc, low-passed
    at 95% of the lower Nyquist (7.6 kHz for 22050 -> 16000) and normalised to
    unity gain at DC. The output depends only on the audio, never on how it was
    split into chunks: ``feed`` accepts any byte count (a sample may straddle
    two calls) and ``finish`` flushes the tail with zero padding.
    """

    HALF_TAPS = 24
    KAISER_BETA = 8.0

    def __init__(self, src: int, dst: int):
        g = gcd(src, dst)
        self._up = dst // g      # output phases
        self._down = src // g
        half = self.HALF_TAPS
        cutoff = 0.95 * min(src, dst) / 2
        norm = _bessel_i0(self.KAISER_BETA)
        self._phases: list[list[float]] = []
        for p in range(self._up):
            frac = p / self._up
            taps = []
            for k in range(-half + 1, half + 1):
                t = k - frac
                x = 2 * cutoff * t / src
                sinc = 1.0 if x == 0 else math.sin(math.pi * x) / (math.pi * x)
                win = _bessel_i0(self.KAISER_BETA * math.sqrt(max(0.0, 1 - (t / half) ** 2))) / norm
                taps.append(2 * cutoff / src * sinc * win)
            gain = sum(taps)
            self._phases.append([c / gain for c in taps])
        self._buf: list[float] = [0.0] * (half - 1)  # silence before the first sample
        self._base = -(half - 1)                 # absolute index of _buf[0]
        self._real = 0                           # samples fed so far
        self._n = 0                              # next output index
        self._carry = b""

    def feed(self, data: bytes) -> bytes:
        data = self._carry + data
        usable = len(data) & ~1
        self._carry = data[usable:]
        if usable:
            self._buf.extend(map(float, struct.unpack(f"<{usable // 2}h", data[:usable])))
            self._real += usable // 2
        return self._run(None)

    def finish(self) -> bytes:
        """The tail, with the stream padded by silence. A dangling odd byte is dropped."""
        self._carry = b""
        self._buf.extend([0.0] * self.HALF_TAPS)
        return self._run(self._real)

    def _run(self, limit: int | None) -> bytes:
        half, up, down = self.HALF_TAPS, self._up, self._down
        buf, base, n = self._buf, self._base, self._n
        total = base + len(buf)
        out: list[int] = []
        phases = self._phases
        while True:
            i0, p = divmod(n * down, up)
            if i0 + half >= total or (limit is not None and i0 >= limit):
                break
            s = i0 - half + 1 - base
            v = round(_dot(phases[p], buf[s:s + 2 * half]))
            out.append(32767 if v > 32767 else -32768 if v < -32768 else v)
            n += 1
        self._n = n
        drop = (n * down) // up - half + 1 - base
        if drop > 0:
            del buf[:drop]
            self._base = base + drop
        return struct.pack(f"<{len(out)}h", *out) if out else b""


# --- the daemon -------------------------------------------------------------


class Speech:
    """An open, header-checked ``POST /tts`` stream. ``aclose`` is idempotent."""

    def __init__(self, client: httpx.AsyncClient, response: httpx.Response, sample_rate: int):
        self._client = client
        self._response = response
        self.sample_rate = sample_rate
        self.closed = False

    def chunks(self):
        return self._response.aiter_raw()

    async def aclose(self) -> None:
        # Shielded: this runs from a cancelled stream (client disconnect) and
        # must still release the upstream connection promptly.
        self.closed = True
        with anyio.CancelScope(shield=True):
            try:
                await self._response.aclose()
            finally:
                await self._client.aclose()


async def open_speech(text: str, manifest: dict) -> Speech:
    """Start synthesis with the local daemon and return the open stream.

    Errors that precede the audio (daemon down, loading, refusing, answering
    with the wrong rate) are raised here, so the route can still answer with
    a proper status. Never falls back.
    """
    url = f"http://{_LOOPBACK}:{manifest['port']}/tts"
    # trust_env off: a proxy variable must not carry the text off the host.
    client = httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False)
    response = None
    try:
        try:
            request = client.build_request("POST", url, json={"text": text})
            response = await client.send(request, stream=True)
        except (httpx.ConnectError, httpx.TimeoutException):
            raise _unavailable("the text-to-speech daemon is not reachable") from None
        except httpx.HTTPError:
            raise upstream_error("the text-to-speech daemon connection failed") from None
        status = response.status_code
        if status != 200:
            detail = None
            try:
                body = json.loads((await response.aread())[:4096])
                if isinstance(body, dict) and isinstance(body.get("error"), str):
                    detail = body["error"][:200]
            except (ValueError, httpx.HTTPError):
                pass
            if status == 503:
                raise _unavailable(
                    f"the text-to-speech daemon is unavailable ({detail or 'busy'})")
            if 400 <= status < 500:
                raise GatewayError(
                    400, f"the text-to-speech daemon rejected the input ({detail or status})",
                    code="invalid_request")
            raise upstream_error(f"the text-to-speech daemon failed ({detail or status})")
        rate = response.headers.get("x-sample-rate", "")
        if not rate.isascii() or not rate.isdigit() or int(rate) != manifest["sample_rate"]:
            raise upstream_error(
                f"the text-to-speech daemon sent sample rate {rate[:12] or 'none'!r}, "
                f"the manifest says {manifest['sample_rate']}")
        if response.headers.get("x-channels") != "1":
            raise upstream_error("the text-to-speech daemon did not send mono audio")
    except BaseException:
        with anyio.CancelScope(shield=True):
            if response is not None:
                await response.aclose()
            await client.aclose()
        raise
    return Speech(client, response, manifest["sample_rate"])


class SpeechResponse(StreamingResponse):
    """A ``StreamingResponse`` that always closes its ``Speech``.

    Under ASGI spec < 2.4 a client that disconnects before the first chunk is
    pulled cancels the response before ``stream_pcm`` ever starts, so that
    generator's ``finally`` never runs. This is the backstop; ``stream_pcm``
    is the normal path and marks the ``Speech`` closed first.
    """

    def __init__(self, speech: Speech, rate: int, encoder: "PcmEncoder | None" = None, **kwargs):
        super().__init__(stream_pcm(speech, rate, encoder), **kwargs)
        self._speech = speech

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if not self._speech.closed:
                await self._speech.aclose()


class PcmEncoder:
    """How PCM leaves the route: ``chunk`` frames each piece, ``finish`` is the trailer.

    The base class is the identity (``stream_format=audio``: raw bytes, no
    trailer). ``finish`` is sent only when the audio ended cleanly, never
    after a mid-stream daemon failure.
    """

    def chunk(self, pcm: bytes) -> bytes:
        return pcm

    def finish(self) -> bytes:
        return b""


class SseEncoder(PcmEncoder):
    """OpenAI ``stream_format=sse``: ``speech.audio.delta`` per chunk, then ``speech.audio.done``.

    Frames are bare ``data: <json>\n\n`` (no ``event:`` line, no ``[DONE]``),
    as OpenAI sends them. The published schema requires ``usage`` on the done
    event; the local engine bills no tokens, so it is all zeros.
    """

    @staticmethod
    def _frame(event: dict) -> bytes:
        return b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n"

    def chunk(self, pcm: bytes) -> bytes:
        return self._frame({"type": "speech.audio.delta",
                            "audio": base64.b64encode(pcm).decode("ascii")})

    def finish(self) -> bytes:
        return self._frame({"type": "speech.audio.done",
                            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}})


async def stream_pcm(speech: Speech, rate: int, encoder: PcmEncoder | None = None):
    """The audio to send at ``rate``, closing the upstream however the stream ends.

    ``encoder`` frames each PCM piece (default: raw PCM). A mid-stream daemon
    failure ends the stream without the encoder's trailer.
    """
    encoder = encoder or PcmEncoder()
    resampler = None if rate == speech.sample_rate else PcmResampler(speech.sample_rate, rate)
    try:
        async for chunk in speech.chunks():
            if resampler is None:
                if chunk:
                    yield encoder.chunk(chunk)
                continue
            # Pure-Python filtering is CPU work: do it off the event loop, a
            # slice at a time so a cancel lands between slices.
            for i in range(0, len(chunk), _SLICE):
                out = await anyio.to_thread.run_sync(resampler.feed, chunk[i:i + _SLICE])
                if out:
                    yield encoder.chunk(out)
        if resampler is not None:
            tail = await anyio.to_thread.run_sync(resampler.finish)
            if tail:
                yield encoder.chunk(tail)
        trailer = encoder.finish()
        if trailer:
            yield trailer
    except httpx.HTTPError:
        # The daemon died or stalled mid-audio (read timeout, dropped
        # connection): the headers are long gone, so end the audio cleanly
        # rather than reset the client. Fixed string: no text, no exception.
        logger.warning("tts: the daemon failed mid-stream; audio ended early")
        return
    except (anyio.get_cancelled_exc_class(), GeneratorExit):
        # The client went away mid-stream (499 semantics): nothing to answer,
        # only the upstream to release. Fixed string: no text, no audio.
        logger.info("tts: client disconnected mid-stream (499); upstream closed")
        raise
    finally:
        await speech.aclose()
