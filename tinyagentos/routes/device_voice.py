"""Device voice routes (spec S6 device part, S6b).

``POST /api/device/v1/voice``      raw PCM16 16 kHz mono in, ``{"text"}`` out  (scope ``voice:stt``)
``POST /api/device/v1/voice/tts``  JSON ``{"text", "sample_rate"?}`` in, streamed PCM16 out (``voice:tts``)

Both call the SAME in-process helpers as the gateway's ``/audio/transcriptions``
and ``/audio/speech`` (``llm_gateway.stt`` / ``llm_gateway.tts``): no HTTP hop
to our own gateway, no cloud fallback, nothing logged or stored (not the text,
not the audio). A device bearer is the only credential: a session user has no
device, so there is no session path here.

Errors use the house device shape, ``{"detail": {"error": <code>, "message": ...}}``,
the same envelope as ``device_scope_missing`` / ``device_tls_required``. The
code strings are the helpers' own (``tts_not_installed``, ``tts_unavailable``,
``stt_not_installed``, ``upstream_error``, ...).

The routes are CSRF-exempt like the other device-bearer routes: the bearer is
not a cookie, so there is no ambient credential to forge.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from tinyagentos.device_auth import device_scope
from tinyagentos.device_scopes import VOICE_STT, VOICE_TTS
from tinyagentos.llm_gateway import stt, tts
from tinyagentos.llm_gateway.errors import GatewayError
from tinyagentos.llm_gateway.router import _TTS_BODY_CAP, _read_capped

router = APIRouter()

_STT_CONTENT_TYPES = {"application/octet-stream", "audio/pcm"}


def _device_error(exc: GatewayError, *, too_large_code: str | None = None) -> HTTPException:
    code = exc.code
    if too_large_code and code == "request_too_large":
        code = too_large_code
    return HTTPException(status_code=exc.status,
                         detail={"error": code or "error", "message": exc.message})


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "invalid_request", "message": message})


@router.post("/api/device/v1/voice/tts")
async def device_tts(request: Request, _device: dict = Depends(device_scope(VOICE_TTS))):
    try:
        raw = await _read_capped(request, _TTS_BODY_CAP)
        try:
            body = json.loads(raw)
        except ValueError:
            raise _bad_request("request body must be a JSON object") from None
        if not isinstance(body, dict):
            raise _bad_request("request body must be a JSON object")
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _bad_request("'text' must be a non-empty string")
        if len(text) > tts.MAX_INPUT_CHARS:
            raise GatewayError(413, f"'text' is over {tts.MAX_INPUT_CHARS} characters",
                               code="input_too_long")
        sample_rate = body.get("sample_rate")
        if "sample_rate" in body and (
                sample_rate is None or isinstance(sample_rate, bool)
                or not isinstance(sample_rate, int)):
            raise _bad_request("'sample_rate' must be an integer or omitted")
        manifest = tts.load_manifest(request.app.state.data_dir)
        rate = tts.check_sample_rate(sample_rate, manifest["sample_rate"])
        if await request.is_disconnected():
            # The client is gone: do not spend the daemon on speech nobody awaits.
            return Response(status_code=499)
        speech = await tts.open_speech(text, manifest)
    except GatewayError as exc:
        raise _device_error(exc, too_large_code="input_too_long") from None
    return tts.SpeechResponse(
        speech, rate,
        media_type="audio/pcm",
        headers={"X-Sample-Rate": str(rate), "X-Channels": "1"},
    )


@router.post("/api/device/v1/voice")
async def device_stt(request: Request, _device: dict = Depends(device_scope(VOICE_STT))):
    ctype = request.headers.get("content-type")
    if ctype is not None and ctype.split(";", 1)[0].strip().lower() not in _STT_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail={
            "error": "unsupported_media_type",
            "message": "send raw PCM16 16 kHz mono as application/octet-stream or audio/pcm"})
    try:
        pcm = await _read_capped(request, stt.MAX_PCM_BYTES)
        if not pcm:
            raise GatewayError(400, "no audio", code="invalid_audio")
        if len(pcm) % 2:
            raise GatewayError(400, "PCM16 audio must have an even number of bytes",
                               code="invalid_audio")
        manifest = stt.load_manifest(request.app.state.data_dir)
        if await request.is_disconnected():
            return Response(status_code=499)
        text = await stt.transcribe_pcm(pcm, manifest)
    except GatewayError as exc:
        raise _device_error(exc, too_large_code="audio_too_large") from None
    return {"text": text}
