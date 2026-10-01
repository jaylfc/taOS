"""Local speech-to-text: the helpers behind ``POST /audio/transcriptions``.

Pure and route-free on purpose: the gateway route and a later device voice
route both import ``load_manifest`` / ``wav_to_pcm`` / ``transcribe_pcm`` and
add only their own transport.

The engine is ``taos-sttd``, a resident daemon on THIS host. It takes raw PCM16
little-endian, mono, 16 kHz at ``POST /stt`` (``Content-Length`` required, at
most ``MAX_PCM_BYTES``, i.e. 30 s) and answers ``{"text": ...}``. Its installer
writes ``<data_dir>/voice/stt/manifest.json`` (``TAOS_STT_MANIFEST`` overrides
the path, read at call time); from it we take the port and the model name,
nothing else.

Hard rules, each pinned by a test:

- The host is the literal ``127.0.0.1``. A manifest cannot point the gateway
  at another machine (no ``host`` key is ever read).
- No fallback of any kind. When the daemon is down, busy or loading the
  caller gets a 503; the audio is never handed to a cloud backend, another
  route or a second attempt. This module imports nothing from the chat
  routing (no litellm config, no failover, no lazy backend proxy).
- Audio and transcripts are never logged, and this module does not log.

Model names (a PROPOSAL, for review): the alias ``taos-stt-default``, like
``taos-default`` and ``taos-embedding-default`` for chat and embeddings, or the
manifest's own ``model`` value. Any other name is a 404 ``model_not_found``.
The caller's key must list the name it asks for (``may_use``).
"""
from __future__ import annotations

import io
import json
import os
import struct
import wave
from pathlib import Path

import httpx

from tinyagentos.llm_gateway.errors import GatewayError, upstream_error

STT_ALIAS = "taos-stt-default"
MANIFEST_ENV = "TAOS_STT_MANIFEST"
# 30 s of 16 kHz mono PCM16: the daemon's own limit.
MAX_PCM_BYTES = 960000
SAMPLE_RATE = 16000
_LOOPBACK = "127.0.0.1"
# The daemon decodes a full 30 s clip on an SBC CPU: allow it, but fail a
# dead socket fast.
_TIMEOUT = httpx.Timeout(60.0, connect=2.0)


def _invalid_audio(message: str) -> GatewayError:
    return GatewayError(400, message, code="invalid_audio")


def _unavailable(message: str) -> GatewayError:
    return GatewayError(503, message, type="api_error", code="stt_unavailable")


def manifest_path(data_dir) -> Path:
    override = os.environ.get(MANIFEST_ENV)
    if override:
        return Path(override)
    return Path(data_dir) / "voice" / "stt" / "manifest.json"


def load_manifest(data_dir) -> dict:
    """The installer's manifest, validated.

    Missing is 409 ``stt_not_installed`` (a state the user can fix by
    installing). Present but unusable is 503 ``stt_unavailable``: a corrupt
    file must never read as "not installed", which would send people to
    reinstall over a half-written one.
    """
    path = manifest_path(data_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise GatewayError(
            409, "speech-to-text is not installed on this device",
            code="stt_not_installed",
        ) from None
    except OSError:
        raise _unavailable("the speech-to-text manifest cannot be read") from None
    try:
        manifest = json.loads(raw)
    except ValueError:
        raise _unavailable("the speech-to-text manifest is not valid JSON") from None
    if not isinstance(manifest, dict):
        raise _unavailable("the speech-to-text manifest is not a JSON object")
    port = manifest.get("port")
    # bool is an int subclass; True is not a port.
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise _unavailable("the speech-to-text manifest has no valid port")
    model = manifest.get("model")
    if not isinstance(model, str) or not model:
        raise _unavailable("the speech-to-text manifest has no model name")
    return manifest


def _data_chunk_size(data: bytes) -> int | None:
    """The declared size of the RIFF ``data`` chunk, which ``wave`` hides.

    ``wave`` rounds an odd size down to whole frames and ``readframes`` quietly
    returns less than the header promised, so an odd length is only visible
    in the chunk header itself.
    """
    pos = 12
    while pos + 8 <= len(data):
        cid, size = struct.unpack_from("<4sI", data, pos)
        if cid == b"data":
            return size
        pos += 8 + size + (size & 1)
    return None


def wav_to_pcm(data: bytes) -> bytes:
    """The raw PCM16 mono 16 kHz frames of a WAV file, or a 400 ``invalid_audio``."""
    try:
        wf = wave.open(io.BytesIO(data), "rb")
    except EOFError:
        raise _invalid_audio("the WAV file is truncated") from None
    except wave.Error as exc:
        text = str(exc)
        if "RIFF" in text:
            raise _invalid_audio("not a RIFF file: expected a WAV file") from None
        if "WAVE" in text:
            raise _invalid_audio("RIFF file is not a WAVE file") from None
        if "unknown format" in text:
            raise _invalid_audio("the WAV file is not PCM") from None
        raise _invalid_audio("the WAV file is truncated or malformed") from None
    with wf:
        if wf.getcomptype() != "NONE":
            raise _invalid_audio("the WAV file is not PCM")
        if wf.getsampwidth() != 2:
            raise _invalid_audio("the WAV file must be 16-bit")
        if wf.getnchannels() != 1:
            raise _invalid_audio("the WAV file must be mono")
        if wf.getframerate() != SAMPLE_RATE:
            raise _invalid_audio(f"the WAV file must be {SAMPLE_RATE} Hz")
        nframes = wf.getnframes()
        if nframes == 0:
            raise _invalid_audio("the WAV file has no audio")
        declared = _data_chunk_size(data)
        if declared is not None and declared % 2:
            raise _invalid_audio("the WAV data length is odd")
        frames = wf.readframes(nframes)
    if len(frames) != nframes * 2:
        raise _invalid_audio("the WAV audio data is truncated")
    return frames


async def transcribe_pcm(pcm: bytes, manifest: dict) -> str:
    """Transcribe raw PCM16 mono 16 kHz with the local daemon. Never falls back."""
    if not pcm:
        raise _invalid_audio("no audio")
    if len(pcm) > MAX_PCM_BYTES:
        raise GatewayError(
            413, f"audio is over {MAX_PCM_BYTES} bytes (30 seconds)",
            code="audio_too_large",
        )
    if len(pcm) % 2:
        raise _invalid_audio("PCM16 audio must have an even number of bytes")
    url = f"http://{_LOOPBACK}:{manifest['port']}/stt"
    try:
        # trust_env off: a proxy variable must not carry audio off the host.
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            resp = await client.post(
                url, content=pcm,
                headers={"Content-Type": "application/octet-stream",
                         "X-Sample-Rate": str(SAMPLE_RATE)},
            )
    except (httpx.ConnectError, httpx.TimeoutException):
        raise _unavailable("the speech-to-text daemon is not reachable") from None
    except httpx.HTTPError:
        raise upstream_error("the speech-to-text daemon connection failed") from None
    try:
        body = resp.json()
    except ValueError:
        body = None
    detail = body.get("error") if isinstance(body, dict) else None
    detail = detail[:200] if isinstance(detail, str) else None
    if resp.status_code == 503:
        raise _unavailable(f"the speech-to-text daemon is unavailable ({detail or 'busy'})")
    if 400 <= resp.status_code < 500:
        raise _invalid_audio(
            f"the speech-to-text daemon rejected the audio ({detail or resp.status_code})")
    if resp.status_code != 200:
        raise upstream_error(f"the speech-to-text daemon failed ({detail or resp.status_code})")
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise upstream_error("the speech-to-text daemon sent an unusable answer")
    return text
