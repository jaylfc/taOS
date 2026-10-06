from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.requests import ClientDisconnect

from tinyagentos.llm_gateway.auth import GatewayCaller, gateway_caller
from tinyagentos.llm_gateway.errors import (
    GatewayError,
    bad_request,
    handle_gateway_error,
    model_not_found,
    model_not_permitted,
)
from tinyagentos.llm_gateway.forward import (
    OLLAMA_PROVIDERS,
    forwardable,
    chat_completion,
    chat_completion_stream,
    resolve_api_key,
)
from tinyagentos.llm_gateway.anthropic import chat_completion_anthropic
from tinyagentos.llm_gateway import stt, tts
from tinyagentos.llm_gateway.resolve import TAOS_DEFAULT, find_routes, model_names, routing_table
import tinyagentos.llm_gateway.resolve as resolve_mod

PREFIX = "/api/llm/v1"
OPENAI_PROVIDER = "openai"

router = APIRouter(prefix=PREFIX)


def mount(app, dependencies=None) -> None:
    app.include_router(router, dependencies=dependencies or [])
    app.add_exception_handler(GatewayError, handle_gateway_error)


def _forwardable(route) -> bool:
    return forwardable(route)


def _model_entry(name: str) -> dict:
    return {"id": name, "object": "model", "created": 0, "owned_by": "taos"}


@router.get("/models")
async def list_models(request: Request, caller: GatewayCaller = Depends(gateway_caller)):
    names = [TAOS_DEFAULT] + [
        n for n in model_names(routing_table(request.app.state)) if n != TAOS_DEFAULT
    ]
    return {"object": "list", "data": [_model_entry(n) for n in names if caller.may_use(n)]}


async def _read_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any parse failure is the caller's
        raise bad_request("request body must be a JSON object") from None
    if not isinstance(body, dict):
        raise bad_request("request body must be a JSON object")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise bad_request("'model' must be a non-empty string")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise bad_request("'messages' must be a non-empty array")
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or not isinstance(msg.get("role"), str):
            raise bad_request(f"'messages[{i}]' must be an object with a string 'role'")
    stream = body.get("stream")
    if stream is not None and not isinstance(stream, bool):
        raise bad_request("'stream' must be a boolean")
    if body.get("tools") is not None and not isinstance(body["tools"], list):
        raise bad_request("'tools' must be an array")
    return body


@router.post("/chat/completions")
async def chat_completions(request: Request, caller: GatewayCaller = Depends(gateway_caller)):
    body = await _read_body(request)
    requested = body["model"]
    if not caller.may_use(requested):
        raise model_not_permitted(requested)
    state = request.app.state
    name = requested
    if requested == TAOS_DEFAULT:
        name = await resolve_mod.default_chat_model(state)
        if name is None:
            raise model_not_found(
                "taos-default has nothing behind it: no default chat model is set. "
                "Pick one in the taOS agent settings."
            )
    routes = find_routes(routing_table(state), name)
    if not routes:
        suffix = f" (taos-default points at it)" if name != requested else ""
        raise model_not_found(f"model {name!r} not found{suffix}")
    route = routes[0]
    alias_grant = requested == TAOS_DEFAULT and caller.may_use(TAOS_DEFAULT)
    if route.model_name != requested and not alias_grant and not caller.may_use(route.model_name):
        raise model_not_permitted(route.model_name)

    # Route through appropriate handler based on provider
    if route.provider == "anthropic":
        principal = caller.caller_id
        if body.get("stream"):
            from fastapi.responses import StreamingResponse
            from tinyagentos.llm_gateway.anthropic import chat_completion_stream_anthropic
            gen = chat_completion_stream_anthropic(routes, body, principal, state)
            try:
                first = await gen.__anext__()
            except StopAsyncIteration:
                return StreamingResponse((), media_type="text/event-stream", headers={"cache-control": "no-cache"})
            except GatewayError as exc:
                return exc.response()

            async def _full():
                yield first
                async for chunk in gen:
                    yield chunk

            return StreamingResponse(_full(), media_type="text/event-stream", headers={"cache-control": "no-cache"})
        api_key = await resolve_api_key(state, route.api_key_ref)
        return JSONResponse(await chat_completion_anthropic(routes, body, api_key, principal, state))

    # Default to OpenAI-compatible handler
    if not _forwardable(route):
        raise GatewayError(
            501,
            f"model {route.model_name!r} is served by a {route.provider or 'unknown'!r} backend; "
            "the taOS gateway only forwards to OpenAI-compatible backends so far",
            code="backend_not_supported",
        )
    # Failover only ever reaches backends the gateway can speak to; each one
    # is sent ONLY its own key, resolved per attempt in forward.py.
    routes = [r for r in routes if _forwardable(r)]
    principal = caller.caller_id
    if body.get("stream"):
        return await chat_completion_stream(routes, body, principal, state)
    return JSONResponse(await chat_completion(routes, body, principal, state))


@router.post("/embeddings")
async def embeddings(request: Request, caller: GatewayCaller = Depends(gateway_caller)):
    """OpenAI ``/v1/embeddings`` from the gateway itself (no LiteLLM).

    The same allowlist rule as chat: the requested name must be in the
    caller's scope. ``taos-embedding-default`` is a name like any other, so an
    agent embeds through it only when its key allows it.
    """
    from tinyagentos.llm_gateway import embeddings as emb

    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001 - any parse failure is the caller's
        raise bad_request("request body must be a JSON object") from None
    body = emb.validate_body(raw)
    requested = body["model"]
    if not caller.may_use(requested):
        raise model_not_permitted(requested)
    state = request.app.state
    routes = emb.find_embedding_routes(await emb.embedding_table(state), requested)
    if not routes:
        raise model_not_found(f"model {requested!r} not found")
    usable = [r for r in routes if emb.servable(r)]
    if not usable:
        route = routes[0]
        raise GatewayError(
            501,
            f"model {route.model_name!r} is served by a {route.provider or 'unknown'!r} backend; "
            "the taOS gateway embeds only through OpenAI-compatible and Ollama-shaped backends",
            code="backend_not_supported",
        )
    return JSONResponse(await emb.create_embedding(usable, body, requested, caller.caller_id, state))


# Room for the WAV header and the multipart framing around the audio.
_STT_FRAMING_SLACK = 64 * 1024
_STT_BODY_CAP = stt.MAX_PCM_BYTES + _STT_FRAMING_SLACK
# The text fields (model, response_format) are a few bytes; this bounds a hostile one.
_STT_FIELD_CAP = 1024
_STT_FIELDS = {"file", "model", "response_format", "stream"}


async def _read_capped(request: Request, cap: int) -> bytes:
    """The request body in memory, 413 the moment it passes ``cap``.

    Deliberately not ``request.form()`` / ``UploadFile``: Starlette would spool
    the audio part to a temp file before the handler runs, so the cap could not
    stop the read and audio would touch disk. A declared Content-Length over
    the cap is refused before a byte is read; without one the stream is cut at
    the cap.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_big = int(declared) > cap
        except ValueError:
            raise bad_request("invalid Content-Length") from None
        if too_big:
            raise GatewayError(413, f"request body over {cap} bytes", code="request_too_large")
    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > cap:
                raise GatewayError(413, f"request body over {cap} bytes", code="request_too_large")
            chunks.append(chunk)
    except ClientDisconnect:
        # Gone mid-upload: the same 499 the routes give a client gone after the read.
        raise GatewayError(499, "client closed the request", code="client_closed_request") from None
    return b"".join(chunks)


def _parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    """``{field: bytes}`` for the transcription fields, from an in-memory body.

    python-multipart's low-level ``MultipartParser`` with in-memory callbacks
    (its ``FormParser`` would spool a large part to a temp file). Unknown
    fields are dropped; a repeated or oversized wanted field is a 400.
    """
    from python_multipart.multipart import MultipartParser, parse_options_header

    mime, params = parse_options_header(content_type.encode("latin-1", "replace"))
    boundary = params.get(b"boundary")
    if mime.lower() != b"multipart/form-data" or not boundary:  # RFC 9110: case-insensitive
        raise bad_request("expected a multipart/form-data body with 'file' and 'model' fields")
    fields: dict[str, bytearray] = {}
    state: dict = {"hname": b"", "hvalue": b"", "headers": {}, "name": None, "ended": False}

    def on_part_begin():
        state.update(hname=b"", hvalue=b"", headers={}, name=None)

    def on_header_field(data, start, end):
        state["hname"] += data[start:end]

    def on_header_value(data, start, end):
        state["hvalue"] += data[start:end]

    def on_header_end():
        state["headers"][state["hname"].lower()] = state["hvalue"]
        state.update(hname=b"", hvalue=b"")

    def on_headers_finished():
        disposition = state["headers"].get(b"content-disposition", b"")
        _, opts = parse_options_header(disposition)
        name = opts.get(b"name", b"").decode("utf-8", "replace")
        if name in _STT_FIELDS:
            if name in fields:
                raise bad_request(f"field {name!r} sent more than once")
            fields[name] = bytearray()
            state["name"] = name

    def on_part_data(data, start, end):
        name = state["name"]
        if name is None:
            return
        fields[name] += data[start:end]
        if name != "file" and len(fields[name]) > _STT_FIELD_CAP:
            raise bad_request(f"field {name!r} is too long")

    def on_end():
        state["ended"] = True

    parser = MultipartParser(boundary, {
        "on_part_begin": on_part_begin, "on_header_field": on_header_field,
        "on_header_value": on_header_value, "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished, "on_part_data": on_part_data,
        "on_end": on_end,
    })
    try:
        parser.write(body)
        parser.finalize()
    except GatewayError:
        raise
    except Exception:  # noqa: BLE001 - any parse failure is the caller's
        raise bad_request("malformed multipart body") from None
    if not state["ended"]:
        raise bad_request("malformed multipart body")
    return {k: bytes(v) for k, v in fields.items()}


@router.post("/audio/transcriptions")
async def audio_transcriptions(request: Request, caller: GatewayCaller = Depends(gateway_caller)):
    """OpenAI ``/v1/audio/transcriptions``, answered by the on-device daemon.

    Multipart ``file`` (16 kHz mono 16-bit PCM WAV, up to 30 s), ``model`` and
    an optional ``response_format`` (``json``, the default, or ``text``) and an
    optional ``stream`` (``true`` answers ``text/event-stream``: one
    ``transcript.text.delta`` with the whole text, then ``transcript.text.done``;
    the local model is batch, and ``stream`` needs ``response_format=json`` as
    OpenAI's streaming models do).
    Local only: nothing here can reach a cloud backend, and neither audio nor
    transcript is logged, traced or kept. See ``stt`` for the rules and the
    model-name proposal.
    """
    body = await _read_capped(request, _STT_BODY_CAP)
    fields = _parse_multipart(request.headers.get("content-type", ""), body)
    wav = fields.get("file")
    if not wav:
        raise bad_request("'file' must be a non-empty WAV upload")
    try:
        requested = fields.get("model", b"").decode("utf-8").strip()
        response_format = fields.get("response_format", b"json").decode("utf-8").strip() or "json"
        stream_raw = fields.get("stream", b"false").decode("utf-8").strip().lower()
    except UnicodeDecodeError:
        raise bad_request("form fields must be UTF-8") from None
    if not requested:
        raise bad_request("'model' must be a non-empty string")
    if response_format not in ("json", "text"):
        raise bad_request("'response_format' must be 'json' or 'text'")
    if stream_raw not in ("true", "false", ""):
        raise bad_request("'stream' must be 'true' or 'false'")
    stream = stream_raw == "true"
    if stream and response_format != "json":
        raise bad_request("'stream' requires 'response_format' to be 'json'")
    if not caller.may_use(requested):
        raise model_not_permitted(requested)
    data_dir = request.app.state.data_dir
    try:
        manifest = stt.load_manifest(data_dir)
    except GatewayError as exc:
        # Not installed: no name can match, so an unknown name is a 404, not a 409.
        if exc.code == "stt_not_installed" and requested != stt.STT_ALIAS:
            raise model_not_found(f"model {requested!r} not found") from None
        raise
    if requested not in (stt.STT_ALIAS, manifest["model"]):
        raise model_not_found(f"model {requested!r} not found")
    pcm = stt.wav_to_pcm(wav)
    if await request.is_disconnected():
        # The client is gone: do not spend the daemon on audio nobody awaits.
        return Response(status_code=499)
    text = await stt.transcribe_pcm(pcm, manifest)
    if stream:
        # The daemon is batch and has already answered, so every error above
        # was a plain JSON response; what streams is the finished text.
        events = (
            {"type": "transcript.text.delta", "delta": text},
            {"type": "transcript.text.done", "text": text},
        )
        return StreamingResponse(
            (b"data: " + json.dumps(e, separators=(",", ":")).encode() + b"\n\n" for e in events),
            media_type="text/event-stream",
        )
    if response_format == "text":
        return PlainTextResponse(text)
    return JSONResponse({"text": text})


# A JSON body of up to MAX_INPUT_CHARS characters, even all \uXXXX escapes (6 bytes
# each), plus the small fields around it.
_TTS_BODY_CAP = tts.MAX_INPUT_CHARS * 12 + 16 * 1024


@router.post("/audio/speech")
async def audio_speech(request: Request, caller: GatewayCaller = Depends(gateway_caller)):
    """OpenAI ``/v1/audio/speech``, answered by the on-device daemon.

    JSON ``model``, ``input`` (the text), optional ``voice`` (``cori``),
    ``response_format`` (``pcm``, the default and only one) and ``sample_rate``
    (the voice's native rate, the default, or 16000, resampled here). The reply
    streams raw PCM16 mono and always says its rate in ``X-Sample-Rate``.
    ``stream_format`` is ``audio`` (the default, as above) or ``sse``: the same
    PCM as ``speech.audio.delta`` events (base64) then ``speech.audio.done``.
    Local only: nothing here can reach a cloud backend, and the input text is
    never logged, traced or kept. See ``tts`` for the rules.
    """
    raw = await _read_capped(request, _TTS_BODY_CAP)
    try:
        body = json.loads(raw)
    except ValueError:
        raise bad_request("request body must be a JSON object") from None
    if not isinstance(body, dict):
        raise bad_request("request body must be a JSON object")
    requested = body.get("model")
    if not isinstance(requested, str) or not requested.strip():
        raise bad_request("'model' must be a non-empty string")
    requested = requested.strip()
    text = body.get("input")
    if not isinstance(text, str) or not text.strip():
        raise bad_request("'input' must be a non-empty string")
    if len(text) > tts.MAX_INPUT_CHARS:
        raise GatewayError(413, f"'input' is over {tts.MAX_INPUT_CHARS} characters",
                           code="input_too_long")
    if "voice" in body and body["voice"] != tts.TTS_VOICE:
        raise bad_request(f"'voice' must be {tts.TTS_VOICE!r} or omitted")
    if "response_format" in body and body["response_format"] != "pcm":
        raise bad_request("'response_format' must be 'pcm' or omitted")
    stream_format = body.get("stream_format", "audio")
    if stream_format not in ("audio", "sse") or not isinstance(stream_format, str):
        raise bad_request("'stream_format' must be 'audio' or 'sse'")
    sample_rate = body.get("sample_rate")
    if "sample_rate" in body and (
            sample_rate is None or isinstance(sample_rate, bool) or not isinstance(sample_rate, int)
            or sample_rate <= 0):
        raise bad_request("'sample_rate' must be a positive integer or omitted")
    if not caller.may_use(requested):
        raise model_not_permitted(requested)
    data_dir = request.app.state.data_dir
    try:
        manifest = tts.load_manifest(data_dir)
    except GatewayError as exc:
        # Not installed: no name can match, so an unknown name is a 404, not a 409.
        if exc.code == "tts_not_installed" and requested != tts.TTS_ALIAS:
            raise model_not_found(f"model {requested!r} not found") from None
        raise
    if requested not in (tts.TTS_ALIAS, manifest["model"]):
        raise model_not_found(f"model {requested!r} not found")
    rate = tts.check_sample_rate(sample_rate, manifest["sample_rate"])
    if await request.is_disconnected():
        # The client is gone: do not spend the daemon on speech nobody awaits.
        return Response(status_code=499)
    speech = await tts.open_speech(text, manifest)
    sse = stream_format == "sse"
    return tts.SpeechResponse(
        speech, rate, tts.SseEncoder() if sse else None,
        media_type="text/event-stream" if sse else "audio/pcm",
        headers={"X-Sample-Rate": str(rate), "X-Channels": "1"},
    )
