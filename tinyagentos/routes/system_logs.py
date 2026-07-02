"""Logs app backend (#1548 part 1): GET /api/system-logs/*.

Operator-facing log viewer so end users never need a terminal to
self-diagnose a broken install. Four backend sources:

- controller / rkllama / qmd: journald units, read via `journalctl`.
- llmproxy: a plain file under the LLMProxy's config_dir.
- client: advertised only here; the existing GET /api/client-logs stays the
  reader for that tab (this module does not reimplement it).

Every line that leaves this module goes through `redact()` first, so a
credential that leaked into a dependency's log or a stack trace never
reaches an operator's clipboard. See tinyagentos/log_redaction.py.

Auth: this is an operator surface, so every route requires an admin session
(`current_user` dependency, `user.is_admin` check), matching client_logs.py.
"""
from __future__ import annotations

import asyncio
import json
import platform
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from tinyagentos import __version__
from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.log_redaction import redact, redact_lines

router = APIRouter()

# Fixed allowlist of journald-backed sources. Never string-interpolate a path
# param into a subprocess argv -- only unit names looked up from this dict
# are ever passed to journalctl.
SOURCE_UNITS = {
    "controller": "tinyagentos.service",
    "rkllama": "rkllama.service",
    "qmd": "qmd.service",
}

SOURCE_LABELS = {
    "controller": "Controller",
    "rkllama": "rkllama",
    "qmd": "qmd",
    "llmproxy": "LLM proxy",
    "client": "Client errors",
}

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# Bound how much a single journalctl call can hand back, independent of the
# `lines` clamp, so a pathological line (or a lot of small ones) cannot blow
# up memory.
_MAX_STDOUT_BYTES = 2_000_000
_MAX_KNOWN_SECRETS = 200
_BUNDLE_LINES = 200


def _journalctl_missing() -> bool:
    """True if there is no journalctl binary on this box.

    Factored out so the "no journald on this install" case can be checked
    once per /sources request instead of once per unit, and so tests can
    monkeypatch it directly instead of needing a real systemd.
    """
    return shutil.which("journalctl") is None


async def _run_journalctl(
    args: "list[str]", *, timeout: float = 10.0, max_bytes: int = _MAX_STDOUT_BYTES
) -> "tuple[int, bytes]":
    """Run `journalctl <args>` (argv-list, never shell=True) and return
    (returncode, capped stdout bytes).

    This is the single seam every journald read goes through, so tests can
    monkeypatch it to return canned output instead of shelling out for real.
    Raises FileNotFoundError if journalctl is not installed. Kills the
    process on timeout so nothing is left running past the deadline.
    """
    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def _read_capped() -> bytes:
        chunks: list[bytes] = []
        total = 0
        while total < max_bytes:
            chunk = await proc.stdout.read(min(65536, max_bytes - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    try:
        stdout = await asyncio.wait_for(_read_capped(), timeout=timeout)
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, stdout


async def _probe_unit_available(unit: str) -> bool:
    """Cheap "does this unit have a journal" probe. Never raises."""
    try:
        rc, _ = await _run_journalctl(["-u", unit, "-n", "0", "--no-pager"], timeout=3.0)
        return rc == 0
    except Exception:
        return False


def _llmproxy_log_path(request: Request) -> "Path | None":
    """The LLMProxy's stderr log, or None if the proxy isn't wired up yet.

    Deliberately reads `.config_dir` (not `.data_dir`): LLMProxy defaults
    config_dir to /tmp/taos-litellm when not overridden, and that's where it
    actually writes litellm.stderr.log (see llm_proxy.py's start()).
    """
    proxy = getattr(request.app.state, "llm_proxy", None)
    config_dir = getattr(proxy, "config_dir", None)
    if not config_dir:
        return None
    return Path(config_dir) / "litellm.stderr.log"


async def _known_secret_values(request: Request) -> "list[str] | None":
    """Best-effort literal secret values for redact()'s known_values pass.

    Never lets a slow or broken secrets store block a log page: any failure
    (missing store, decrypt error, timeout) returns None so the caller falls
    back to pattern-only redaction. Skipped entirely above a size cap so a
    huge secrets store cannot stall every log page load.
    """
    store = getattr(request.app.state, "secrets", None)
    if store is None:
        return None
    try:
        entries = await store.list()
        if len(entries) > _MAX_KNOWN_SECRETS:
            return None
        values: list[str] = []
        for entry in entries:
            name = entry.get("name")
            if not name:
                continue
            full = await store.get(name)
            if full and full.get("value"):
                values.append(full["value"])
        return values or None
    except Exception:
        return None


def _clamp_lines(lines: int) -> int:
    """Clamp (not reject) out-of-range `lines` -- friendlier than a 400 for a
    UI-driven page size, and the cap keeps a runaway request bounded."""
    return max(1, min(lines, 2000))


async def _list_sources(request: Request) -> "list[dict]":
    """The sources available on this install. Used by both GET /sources and
    the bug-report bundle so they never drift apart."""
    result = []
    journald_absent = _journalctl_missing()
    for source_id in ("controller", "rkllama", "qmd"):
        unit = SOURCE_UNITS[source_id]
        available = False if journald_absent else await _probe_unit_available(unit)
        result.append(
            {
                "id": source_id,
                "label": SOURCE_LABELS[source_id],
                "kind": "journald" if available else "file",
                "available": available,
            }
        )

    llmproxy_path = _llmproxy_log_path(request)
    result.append(
        {
            "id": "llmproxy",
            "label": SOURCE_LABELS["llmproxy"],
            "kind": "file",
            "available": bool(llmproxy_path and llmproxy_path.exists()),
        }
    )
    result.append(
        {
            "id": "client",
            "label": SOURCE_LABELS["client"],
            "kind": "client",
            "available": hasattr(request.app.state, "client_log_store"),
        }
    )
    return result


def _parse_journald_json_lines(stdout: bytes) -> "list[dict]":
    records = []
    for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            records.append(json.loads(raw_line))
        except ValueError:
            continue
    return records


async def _read_journald_page(
    source_id: str, unit: str, lines: int, before: "str | None", known_values: "list[str] | None"
) -> dict:
    # journalctl returns oldest-first within a "-n <lines>" window; there is
    # no direct "entries before this cursor" flag, so this v1 pager treats
    # `before` as a resume token via --after-cursor (a simple, if not fully
    # bidirectional, paging scheme) and reverses the page so the response is
    # newest-first, as the design calls for.
    args = ["-u", unit, "--no-pager", "-o", "json", "-n", str(lines)]
    if before:
        args += ["--after-cursor", before]
    try:
        rc, stdout = await _run_journalctl(args, timeout=10.0)
    except Exception as exc:
        return {"source": source_id, "lines": [], "cursor": None, "error": str(exc)}
    if rc != 0:
        return {"source": source_id, "lines": [], "cursor": None, "error": f"journalctl exited {rc}"}

    records = _parse_journald_json_lines(stdout)
    messages = [str(r.get("MESSAGE", "")) for r in records]
    cursor_out = records[0].get("__CURSOR") if records else None
    messages.reverse()
    return {"source": source_id, "lines": redact_lines(messages, known_values), "cursor": cursor_out}


def _tail_text_lines(data: bytes, n: int, upto: "int | None") -> "tuple[list[str], int | None]":
    """Return the last `n` lines of `data` (optionally truncated at byte
    offset `upto`) plus a cursor marking where the next (older) page starts.
    """
    if upto is not None:
        data = data[: max(0, upto)]
    text = data.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    page = all_lines[-n:] if n < len(all_lines) else all_lines
    remaining = len(all_lines) - len(page)
    if remaining <= 0:
        return page, None
    older_text = "\n".join(all_lines[:remaining]) + "\n"
    cursor = len(older_text.encode("utf-8"))
    return page, cursor


async def _read_file_page(
    source_id: str, path: "Path | None", lines: int, before: "str | None", known_values: "list[str] | None"
) -> dict:
    if path is None or not path.exists():
        return {"source": source_id, "lines": [], "cursor": None, "error": "log file not available"}
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        return {"source": source_id, "lines": [], "cursor": None, "error": str(exc)}

    upto = None
    if before is not None:
        try:
            upto = int(before)
        except ValueError:
            upto = None

    page, cursor_out = _tail_text_lines(data[-_MAX_STDOUT_BYTES:], lines, upto)
    page.reverse()
    return {"source": source_id, "lines": redact_lines(page, known_values), "cursor": cursor_out}


@router.get("/api/system-logs/sources")
async def get_sources(request: Request, user: CurrentUser = Depends(current_user)):
    """The log sources available on THIS install."""
    if not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _list_sources(request)


# Registered before the "/{source}" catch-all below so a literal path here
# (e.g. "bundle") is matched by this route instead of being treated as a
# source id -- Starlette matches routes in registration order, not by
# specificity, so ordering here is load-bearing.
@router.get("/api/system-logs/bundle")
async def get_bundle(request: Request, user: CurrentUser = Depends(current_user)):
    """The copy-for-bug-report payload: environment banner + last N lines of
    every available source, already redacted."""
    if not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    parts = [_environment_banner(), ""]
    known_values = await _known_secret_values(request)
    sources = await _list_sources(request)
    for src in sources:
        if not src["available"]:
            continue
        parts.append(f"=== {src['label']} ===")
        try:
            parts.extend(await _bundle_section(request, src, known_values))
        except Exception as exc:
            parts.append(f"(unavailable: {exc})")
        parts.append("")

    return PlainTextResponse("\n".join(parts), media_type="text/plain")


@router.get("/api/system-logs/{source}")
async def get_source_page(
    source: str,
    request: Request,
    lines: int = 200,
    before: "str | None" = None,
    user: CurrentUser = Depends(current_user),
):
    """Paged, redacted, newest-first read of one source."""
    if not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    n = _clamp_lines(lines)
    known_values = await _known_secret_values(request)
    if source in SOURCE_UNITS:
        return await _read_journald_page(source, SOURCE_UNITS[source], n, before, known_values)
    if source == "llmproxy":
        return await _read_file_page(source, _llmproxy_log_path(request), n, before, known_values)
    # "client" (and anything else) is not a readable source here: client logs
    # are read via the existing GET /api/client-logs endpoint.
    return JSONResponse({"error": "unknown source"}, status_code=400)


async def _journald_tail_gen(unit: str, request: Request):
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "-u",
            unit,
            "-f",
            "-o",
            "json",
            "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        yield f"data: {json.dumps({'error': 'journalctl not available'})}\n\n"
        return

    known_values = await _known_secret_values(request)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            if not raw:
                break
            try:
                rec = json.loads(raw.decode("utf-8", errors="replace"))
                message = str(rec.get("MESSAGE", ""))
            except ValueError:
                message = raw.decode("utf-8", errors="replace").strip()
            line = redact(message, known_values)
            yield f"data: {json.dumps({'line': line})}\n\n"
    finally:
        # Always terminate the tail process -- on disconnect, cancellation,
        # or a plain EOF -- so a closed SSE connection never leaves an
        # orphaned journalctl -f running.
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


async def _file_tail_gen(path: Path, request: Request):
    known_values = await _known_secret_values(request)
    try:
        pos = path.stat().st_size if path.exists() else 0
    except OSError:
        pos = 0
    while True:
        if await request.is_disconnected():
            return
        try:
            size = path.stat().st_size
        except OSError:
            await asyncio.sleep(1.0)
            continue
        if size < pos:
            # File rotated/truncated underneath us; resume from the new end.
            pos = size
        if size > pos:
            try:
                chunk = await asyncio.to_thread(_read_range, path, pos, size)
                pos = size
            except OSError:
                await asyncio.sleep(1.0)
                continue
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                if line:
                    yield f"data: {json.dumps({'line': redact(line, known_values)})}\n\n"
        await asyncio.sleep(1.0)


def _read_range(path: Path, start: int, end: int) -> bytes:
    with path.open("rb") as f:
        f.seek(start)
        return f.read(end - start)


@router.get("/api/system-logs/{source}/stream")
async def stream_source(source: str, request: Request, user: CurrentUser = Depends(current_user)):
    """SSE live tail of one source."""
    if not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if source in SOURCE_UNITS:
        gen = _journald_tail_gen(SOURCE_UNITS[source], request)
        return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)
    if source == "llmproxy":
        path = _llmproxy_log_path(request)
        if path is None:
            return JSONResponse({"error": "llmproxy log not available"}, status_code=400)
        gen = _file_tail_gen(path, request)
        return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)
    return JSONResponse({"error": "unknown source"}, status_code=400)


def _environment_banner() -> str:
    distro = None
    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            for line in os_release.read_text().splitlines():
                if line.startswith("PRETTY_NAME="):
                    distro = line.split("=", 1)[1].strip().strip('"')
                    break
        except OSError:
            distro = None
    if not distro:
        distro = platform.platform()

    lines = [f"distro: {distro}", f"kernel: {platform.uname().release}"]

    board_path = Path("/proc/device-tree/model")
    if board_path.exists():
        try:
            board = board_path.read_bytes().rstrip(b"\x00").decode("utf-8", errors="replace")
            if board:
                lines.append(f"board: {board}")
        except OSError:
            pass

    lines.append(f"python: {sys.version.split()[0]}")
    lines.append(f"taOS version: {__version__}")
    return "\n".join(lines)


async def _bundle_section(request: Request, src: dict, known_values: "list[str] | None") -> "list[str]":
    """Redacted last-N lines for one source, for the bug-report bundle.

    In-process reuse of the same page readers the /api/system-logs/{source}
    endpoint uses -- no HTTP self-call.
    """
    source_id = src["id"]
    if source_id == "client":
        store = getattr(request.app.state, "client_log_store", None)
        if store is None:
            return ["(unavailable: client log store not initialized)"]
        items = await store.list_recent(limit=_BUNDLE_LINES)
        raw = [f"[{item.get('level', '')}] {item.get('message', '')}" for item in items]
        return redact_lines(raw, known_values)
    if source_id in SOURCE_UNITS:
        page = await _read_journald_page(
            source_id, SOURCE_UNITS[source_id], _BUNDLE_LINES, None, known_values
        )
    elif source_id == "llmproxy":
        page = await _read_file_page(
            source_id, _llmproxy_log_path(request), _BUNDLE_LINES, None, known_values
        )
    else:
        return []
    if page.get("error"):
        return [f"(unavailable: {page['error']})"]
    return page["lines"]
