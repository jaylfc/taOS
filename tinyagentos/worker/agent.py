from __future__ import annotations
import asyncio
import ipaddress
import json
import logging
import os
import platform
import socket
import time
from pathlib import Path
from urllib.parse import urlparse, urlsplit
import httpx
import psutil

logger = logging.getLogger(__name__)

# Sentinel returned by register() when the controller rejects the signing key
# with a known pairing error (worker_not_paired or bad_signature).
# Distinct from False (generic failure) and True (success).
_NEEDS_REPAIR = 401

# How long to wait between re-pair attempts (seconds).
_REPAIR_INTERVAL = 60

# How often to re-log the re-pair instruction while in the needs-re-pair state.
_REPAIR_LOG_INTERVAL = 300  # 5 minutes


# Marker dropped by install-worker.sh when it backs up an existing
# taos-worker-pool. The worker forwards it to the controller once on
# registration; controller materialises a notification + a workspace
# text file. Worker deletes the marker after a successful POST so it
# doesn't repeat the alert on every reconnect.
_STORAGE_BACKUP_MARKER = Path("/var/lib/tinyagentos-worker/storage-backup.json")


def _read_storage_backup_marker() -> dict | None:
    """Return the parsed storage-backup marker if present, else None.
    Errors swallowed -- the marker is best-effort plumbing and must not
    break worker registration."""
    try:
        if not _STORAGE_BACKUP_MARKER.exists():
            return None
        return json.loads(_STORAGE_BACKUP_MARKER.read_text())
    except Exception:  # noqa: BLE001
        return None


def _delete_storage_backup_marker() -> None:
    try:
        _STORAGE_BACKUP_MARKER.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _detect_lan_ip(controller_url: str) -> str | None:
    """Return the local IPv4 address the worker would use to reach the
    controller -- same address the controller sees the registration POST
    coming from. Used to populate `host_lan_ip` on registration so the
    install-targets matcher can link an incus remote to its worker even
    when the worker's `url` field points at an unrelated backend (e.g.
    the local Ollama on 127.0.0.1).

    Connectionless UDP: opening a socket and calling ``connect`` makes
    the kernel pick the outbound interface, but no packet is sent -- we
    just read ``getsockname()`` and close. Falls back to ``None`` if
    the controller URL can't be parsed.
    """
    try:
        host = urlparse(controller_url).hostname
        if not host:
            return None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))  # UDP discard port; never delivered
            return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return None


def _is_repair_rejection(resp) -> bool:
    """Return True if the response is a 401 with a pairing-rejection code."""
    if resp.status_code != 401:
        return False
    try:
        code = resp.json().get("code", "")
        return code in {"worker_not_paired", "bad_signature"}
    except Exception:  # noqa: BLE001
        return False


# Backend types that mean "this host has a GPU-class accelerator". Mirrors the
# GPU backend set in tinyagentos/scheduler/discovery.py.
_GPU_BACKEND_TYPES = {"vllm", "ollama", "exo", "mlx"}


def _gpu_type(hardware: dict) -> str | None:
    """Return ``hardware.gpu.type`` from a serialised HardwareProfile.

    Both resources call sites (register and heartbeat) serialise the profile
    with ``asdict()``, so they resolve the GPU class through this one helper
    instead of one attribute access and one dict walk that could drift apart.
    """
    gpu = hardware.get("gpu")
    return gpu.get("type") if isinstance(gpu, dict) else None


def _collect_resources(
    backends: list[dict],
    gpu_type: str | None,
    platform_name: str | None = None,
) -> list[str]:
    """Return the resource classes this worker advertises.

    Mirrors the resource table in docs/design/resource-scheduler.md.
    ``gpu-metal`` is the Apple Silicon class (MLX / llama.cpp Metal / Core ML
    on unified memory); CUDA-class accelerators report ``gpu-cuda-0``.

    Args:
        backends: live backend probe results, each carrying a ``type``.
        gpu_type: ``hardware.gpu.type`` (``"apple"`` on Apple Silicon), or None.
        platform_name: ``platform.system().lower()`` (``"darwin"`` on macOS).
            A Mac that is not Apple Silicon has no CUDA or ROCm device, so a
            CPU-mode Ollama/llama.cpp backend there must not advertise
            ``gpu-cuda-0`` -- that would contradict install-worker.sh's
            Intel-Mac ``cpu-inference`` fallback.

    Installer-detected classes are unioned in from ``TAOS_WORKER_RESOURCES``
    (comma-separated). install-worker.sh exports ``gpu-metal,cpu-inference``
    on Apple Silicon and ``cpu-inference`` on Intel Macs. The union keeps a
    class advertised while its backend is stopped or not yet installed --
    the missing backend shows up as an unavailable capability instead of the
    worker silently vanishing from the scheduler's view.
    """
    resources = ["cpu-inference"]
    if any(b.get("type") == "rkllama" for b in backends):
        resources.append("npu-rk3588")
    if any(b.get("type") in _GPU_BACKEND_TYPES for b in backends):
        if gpu_type == "apple":
            resources.append("gpu-metal")
        elif platform_name == "darwin":
            # Intel Mac: macOS has no CUDA/ROCm class at all. The backend
            # still serves, as cpu-inference.
            pass
        else:
            resources.append("gpu-cuda-0")
    for name in os.environ.get("TAOS_WORKER_RESOURCES", "").split(","):
        name = name.strip()
        if name and name not in resources:
            resources.append(name)
    return resources


# Probe candidates every worker checks. Both the standard upstream ports AND
# the TAOS-namespaced ones: install-worker.sh installs a TAOS-bundled Ollama
# on 21434 to avoid colliding with any existing user Ollama on 11434; we want
# to detect both so the user's pre-existing backends are first-class citizens
# alongside the bundled one. TAOS_EXTRA_BACKENDS and the worker manifest add
# more (#3232).
_DEFAULT_PROBE_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("rkllama", "http://localhost:7833"),
    ("rkllama", "http://localhost:8080"),         # legacy port; existing installs
    ("hailo-ollama", "http://localhost:7836"),    # Hailo-10H NPU LLM (taOS remap; upstream 8000 banned)
    ("ollama", "http://localhost:11434"),         # user / system Ollama (default port)
    ("ollama", "http://localhost:21434"),         # TAOS-bundled Ollama (taos-ollama.service)
    ("llama-cpp", "http://localhost:8000"),
    ("llama-cpp", "http://localhost:18080"),      # TAOS-bundled llama.cpp (future)
    ("vllm", "http://localhost:8000"),
    ("vllm", "http://localhost:18000"),           # TAOS-bundled vLLM (future)
    ("sd-cpp", "http://localhost:7864"),
    ("exo", "http://localhost:52415"),           # exo distributed inference (default port)
)

# Types an extra/manifest candidate may declare: the local servers whose API
# detect_backends can probe (/api/tags, /sdapi/v1/sd-models, /v1/models,
# llama-swap's /running). llama-swap has no default candidate: its default
# port 8080 is also llama.cpp's and rkllama's legacy one, so it is configured.
_PROBEABLE_TYPES = frozenset({
    "rkllama", "ollama", "hailo-ollama", "llama-cpp", "llama-swap", "vllm", "exo", "mlx",
    "sd-cpp",
})
# Types detected by /v1/models alone, which a llama-swap server also answers.
_OPENAI_MODELS_TYPES = frozenset({"llama-cpp", "vllm", "exo", "mlx"})
_MAX_EXTRA_BACKENDS = 16
# Total time one candidate may take. httpx timeouts are per read, so a server
# trickling bytes could otherwise hold the probe (and the heartbeat, which the
# controller marks offline after 30 s) indefinitely. Up to three sequential
# requests at a 3 s timeout each fit inside it.
_PROBE_DEADLINE_S = 10.0

# Each candidate is an outbound request on every heartbeat, so it is held to
# loopback or a private LAN. Explicit ranges, not ipaddress.is_private (which
# also admits documentation/benchmark ranges); link-local is excluded because
# it hosts cloud metadata services.
_LOOPBACK_NETS = tuple(map(ipaddress.ip_network, ("127.0.0.0/8", "::1/128")))
_LAN_NETS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
)))
_DENIED_ADDRESSES = {ipaddress.ip_address("fd00:ec2::254")}  # AWS IPv6 metadata

_warned_probe_entries: set[str] = set()


def _warn_probe_entry_once(message: str) -> None:
    # detect_backends runs every heartbeat; log each distinct problem once.
    if message not in _warned_probe_entries:
        _warned_probe_entries.add(message)
        logger.warning(message)


def _normalize_probe_url(url: str, *, loopback_only: bool = False) -> str:
    """Reduce *url* to ``scheme://host[:port]`` or raise ValueError.

    The host must be ``localhost`` or a literal loopback IP, or (unless
    *loopback_only*) a literal private-LAN IP. Hostnames are never resolved:
    what a name resolves to can change after it is checked.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"malformed URL: {exc}") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("scheme must be http or https")
    if parts.username is not None or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("must be a base URL (no credentials, path, query or fragment)")
    host = (parts.hostname or "").lower()
    if host == "localhost":
        return f"{parts.scheme}://localhost" + (f":{port}" if port is not None else "")
    try:
        if "%" in host:
            raise ValueError
        addr = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("host must be localhost or a literal IP address") from None
    mapped = getattr(addr, "ipv4_mapped", None)
    checked = mapped if mapped is not None else addr
    nets = _LOOPBACK_NETS if loopback_only else _LOOPBACK_NETS + _LAN_NETS
    if checked in _DENIED_ADDRESSES or not any(checked in n for n in nets):
        raise ValueError("host is not a loopback address" if loopback_only
                         else "host is not a loopback or LAN address")
    host_fmt = f"[{addr.compressed}]" if addr.version == 6 else addr.compressed
    return f"{parts.scheme}://{host_fmt}" + (f":{port}" if port is not None else "")


def _parse_extra_backends(raw: str) -> list[tuple[str, str]]:
    """Parse ``TAOS_EXTRA_BACKENDS="type=url,..."``; bad entries are skipped
    with a warning, never raised."""
    out: list[tuple[str, str]] = []
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        backend_type, sep, url = (s.strip() for s in entry.partition("="))
        backend_type = backend_type.lower()
        try:
            if not sep or not backend_type or not url:
                raise ValueError("expected type=url")
            if backend_type not in _PROBEABLE_TYPES:
                raise ValueError(f"type must be one of {', '.join(sorted(_PROBEABLE_TYPES))}")
            out.append((backend_type, _normalize_probe_url(url)))
        except ValueError as exc:
            _warn_probe_entry_once(f"TAOS_EXTRA_BACKENDS: skipping {entry!r}: {exc}")
    if len(out) > _MAX_EXTRA_BACKENDS:
        _warn_probe_entry_once(
            f"TAOS_EXTRA_BACKENDS: {len(out)} entries, only the first "
            f"{_MAX_EXTRA_BACKENDS} are probed"
        )
    return out[:_MAX_EXTRA_BACKENDS]


def _manifest_entry_url(m: dict) -> str | None:
    """The one URL a manifest entry declares: its ``health_url`` origin
    (loopback only, since the manifest describes this machine), else
    ``http://localhost:<port>``. A rejected health_url falls back to the
    port. None when it declares neither validly."""
    health_url, port = m.get("health_url"), m.get("port")
    if isinstance(health_url, str) and health_url.strip():
        try:
            parts = urlsplit(health_url.strip())
            origin = _normalize_probe_url(f"{parts.scheme}://{parts.netloc}", loopback_only=True)
            # The entry is reported under its declared port, so a health_url
            # on another port would split it across two backends.
            if port not in (None, 0) and urlsplit(origin).port != port:
                raise ValueError(f"its port does not match the declared port {port!r}")
            return origin
        except ValueError as exc:
            _warn_probe_entry_once(
                f"worker manifest: ignoring health_url of {m.get('model_id')!r}: {exc}"
            )
    if port in (None, 0):
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        _warn_probe_entry_once(
            f"worker manifest: ignoring port {port!r} of {m.get('model_id')!r}: "
            "not a TCP port number"
        )
        return None
    return f"http://localhost:{port}"


# llama-swap's GET /running is {"running": [{"model": <id>, "state": ..., ...}]}
# (internal/server/api.go handleRunning, verified against llama-swap v261). It
# lists every process not stopped: "starting", "ready" or "stopping" (the
# last is excluded below). Plain
# llama.cpp serves /v1/models but no /running, which is how the two differ.
_LLAMA_SWAP_UNLOADING_STATES = frozenset({"stopping", "stopped", "shutdown"})


def _parse_llama_swap_running(body) -> list[dict] | None:
    """Models a llama-swap ``/running`` reply holds in memory (starting or
    ready), or None when *body* is not that reply. Bad entries are skipped."""
    if not isinstance(body, dict) or not isinstance(body.get("running"), list):
        return None
    out = []
    for entry in body["running"]:
        if not isinstance(entry, dict):
            continue
        model, state = entry.get("model"), entry.get("state")
        if not isinstance(model, str) or not model:
            continue
        if isinstance(state, str) and state in _LLAMA_SWAP_UNLOADING_STATES:
            continue
        out.append({"name": model, "size_mb": 0})
    return out


def _is_local_llama_swap_model(m) -> bool:
    """A /v1/models entry served by this llama-swap. Peer entries
    (``meta.llamaswap.type == "peer"``) run on another machine, and selectors
    may point at peers; builds without that metadata list local models only."""
    if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not m["id"]:
        return False
    meta = m.get("meta")
    info = meta.get("llamaswap") if isinstance(meta, dict) else None
    kind = info.get("type") if isinstance(info, dict) else None
    return kind is None or kind == "model"


async def _probe_llama_swap(client: httpx.AsyncClient,
                            base_url: str) -> tuple[list[dict], list[dict]] | None:
    """(models, loaded_models) from one ``/running`` and one ``/v1/models``,
    or None when *base_url* is not a llama-swap server: without /running it
    is some other OpenAI-compatible server (plain llama.cpp), which its own
    type reports."""
    try:
        resp = await client.get(f"{base_url}/running")
        loaded = _parse_llama_swap_running(resp.json()) if resp.status_code == 200 else None
        if loaded is None:
            return None
        resp = await client.get(f"{base_url}/v1/models")
        data = resp.json() if resp.status_code == 200 else None
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            return None
        models = [{"name": m["id"], "size_mb": 0}
                  for m in data["data"] if _is_local_llama_swap_model(m)]
        return models, loaded
    except Exception:
        return None


def _candidate_key(backend_type: str, url: str) -> tuple:
    """Identity of a normalized candidate: localhost and loopback IPs are one host."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    return (backend_type, parts.scheme, "loopback" if loopback else host, parts.port)


def _dedupe_candidates(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop repeated candidates, keeping the first (default) spelling."""
    seen: set[tuple] = set()
    out = []
    for backend_type, url in candidates:
        key = _candidate_key(backend_type, url)
        if key not in seen:
            seen.add(key)
            out.append((backend_type, url))
    return out


class WorkerAgent:
    def __init__(
        self,
        controller_url: str,
        name: str | None = None,
        worker_port: int = 0,
        extra_capabilities: list[str] | None = None,
        advertise_url: str | None = None,
        state_dir: "Path | None" = None,
    ):
        self.controller_url = controller_url.rstrip("/")
        self.name = name or socket.gethostname()
        self.worker_port = worker_port
        self.extra_capabilities = list(extra_capabilities or [])
        self.advertise_url = advertise_url
        self._running = False
        self._registered = False
        # Worker-initiated lifecycle status (taOS #890 C2).  Persisted across
        # controller restarts so that periodic status-less heartbeats don't
        # silently revert a draining/updating worker back to "online".
        self._lifecycle_status: str | None = None
        self._lifecycle_reason: str | None = None
        self._generation: int | None = None

        from tinyagentos.worker.pairing import default_state_dir, load_signing_key
        self._state_dir = state_dir or default_state_dir()
        self._signing_key: bytes | None = load_signing_key(self._state_dir)

        # Worker update-check service (background version polling).
        # Created eagerly so callers can inspect state; started in run().
        self._update_service: "WorkerUpdateService | None" = None

    async def detect_backends(self) -> list[dict]:
        """Discover locally running inference backends via live probing.

        Backend-driven: each candidate gets a live health check and, on
        success, a live model list so the controller sees what's actually
        loaded right now. Filename conventions and static capability
        declarations are not the source of truth anywhere.
        """
        from tinyagentos.scheduler.backend_catalog import BACKEND_CAPABILITIES
        from tinyagentos.worker import worker_manifest

        # The manifest declares which models this machine *can* run,
        # independently of what is loaded. It is external input: a malformed
        # file or entry must degrade to "no manifest" (logged), never crash
        # detect_backends() and take the whole worker down with it.
        declared: list[tuple[str, str | None, dict]] = []  # (type, probe url, entry)
        try:
            manifest = worker_manifest.load_manifest()
            for m in manifest.get("models") or []:
                if not isinstance(m, dict):
                    continue
                bt = worker_manifest.SOFTWARE_TO_BACKEND_TYPE.get(m.get("software", ""))
                if not bt:
                    continue  # unknown software type, silently skip
                if not m.get("model_id"):
                    logger.warning("skipping worker-manifest entry without model_id: %r", m)
                    continue
                declared.append((bt, _manifest_entry_url(m), m))
        except Exception:  # noqa: BLE001 - manifest must never brick the worker
            logger.warning("worker-manifest enrichment failed; continuing without it",
                           exc_info=True)
            declared = []

        candidates = _dedupe_candidates(
            list(_DEFAULT_PROBE_CANDIDATES)
            + _parse_extra_backends(os.environ.get("TAOS_EXTRA_BACKENDS", ""))
            + [(bt, url) for bt, url, _ in declared if url and bt in _PROBEABLE_TYPES]
        )

        async def probe(client: httpx.AsyncClient, backend_type: str, base_url: str):
            if backend_type == "llama-swap":
                probed = await _probe_llama_swap(client, base_url)
                if probed is None:
                    return None
                models, loaded_models = probed
            else:
                models = await self._probe_models(client, backend_type, base_url)
                if models is None:
                    return None  # backend not running here
                loaded_models = await self._probe_loaded_models(client, backend_type, base_url)
            kv_quant = await self._probe_kv_quant(client, backend_type, base_url)
            _port = urlparse(base_url).port
            return {
                "name": f"{backend_type}:{_port}" if _port is not None else backend_type,
                "type": backend_type,
                "url": base_url,
                "capabilities": sorted(BACKEND_CAPABILITIES.get(backend_type, set())),
                "models": models,
                # Subset of `models` that are actually resident in NPU/GPU/CPU
                # memory right now, so Activity's Loaded Models widget reflects
                # real residency rather than the full catalog of downloads.
                "loaded_models": loaded_models,
                "status": "ok",
                # Per-backend KV quant support, used by the worker to build
                # its cluster-level kv_cache_quant_support advertisement.
                "kv_quant_support": kv_quant,
            }

        # Probed concurrently so dead candidates cost one timeout in total,
        # not one each (the heartbeat must beat the controller's 30 s
        # offline cutoff). LAN candidates get a short connect timeout.
        async def probe_by_deadline(client: httpx.AsyncClient, backend_type: str, base_url: str):
            try:
                return await asyncio.wait_for(probe(client, backend_type, base_url),
                                              _PROBE_DEADLINE_S)
            except asyncio.TimeoutError:
                _warn_probe_entry_once(
                    f"probe of {backend_type} at {base_url} took over "
                    f"{_PROBE_DEADLINE_S:g} s; treating it as down"
                )
                return None

        async with httpx.AsyncClient(timeout=3) as local_client, \
                httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as lan_client:
            results = await asyncio.gather(*(
                probe_by_deadline(
                    local_client if _candidate_key(bt, url)[2] == "loopback" else lan_client,
                    bt, url)
                for bt, url in candidates
            ))
        backends = [b for b in results if b is not None]
        # A llama-swap server also answers the /v1/models probe of the generic
        # types; when one sits on a candidate's endpoint, report it once.
        swap_endpoints = {_candidate_key(b["type"], b["url"])[1:]
                          for b in backends if b["type"] == "llama-swap"}
        backends = [b for b in backends
                    if b["type"] not in _OPENAI_MODELS_TYPES
                    or _candidate_key(b["type"], b["url"])[1:] not in swap_endpoints]

        # Attach declared models to live backends so the controller sees both
        # "loaded" and "available" states. An entry that declares a port or
        # health_url belongs to the backend on that port only; one that
        # declares neither applies to every live backend of its type.
        # Anything left unmatched becomes a synthetic stopped entry, one per
        # declared (type, port), so availability is decoupled from liveness.
        def _available(m: dict, status: str) -> dict:
            return {
                "model_id": m["model_id"],
                "capability": m.get("capability", ""),
                "software": m.get("software", ""),
                "port": m.get("port", 0),
                "vram_required_gb": m.get("vram_required_gb", 0.0),
                "health_url": m.get("health_url", ""),
                "status": status,
            }

        try:
            live = {_candidate_key(b["type"], b["url"]): b for b in backends}
            stopped: dict[tuple, tuple[str, str | None, list[dict]]] = {}
            for bt, url, m in declared:
                if url is not None:
                    match = live.get(_candidate_key(bt, url))
                    targets = [match] if match else []
                else:
                    targets = [b for b in backends if b["type"] == bt]
                for b in targets:
                    # loaded_models (resident in memory, per /api/ps), not the
                    # on-disk catalog, decides "loaded".
                    resident = {x.get("name", "") for x in b.get("loaded_models", [])}
                    status = "loaded" if m["model_id"] in resident else "available"
                    b.setdefault("available_models", []).append(_available(m, status))
                if not targets:
                    key = _candidate_key(bt, url) if url else (bt,)
                    stopped.setdefault(key, (bt, url, []))[2].append(_available(m, "available"))

            for bt, url, available in stopped.values():
                port = urlsplit(url).port if url else None
                backends.append({
                    # Synthetic entry: no probed instance, so the url is None
                    # because there is no reachable endpoint.
                    "name": f"{bt}:{port}" if port is not None else bt,
                    "type": bt,
                    "url": None,
                    "capabilities": sorted(BACKEND_CAPABILITIES.get(bt, set())),
                    "models": [],
                    "loaded_models": [],
                    "status": "stopped",
                    # No probed backend → KV quant support is unknown.
                    # Empty dict means "no data" rather than over-reporting
                    # fp16.  detect_kv_quant_support() adds the fp16
                    # baseline anyway.
                    "kv_quant_support": {},
                    "available_models": available,
                })
        except Exception:  # noqa: BLE001 - manifest must never brick the worker
            logger.warning("worker-manifest enrichment failed; continuing without it",
                           exc_info=True)

        return backends

    async def _probe_kv_quant(
        self, client: httpx.AsyncClient, backend_type: str, base_url: str
    ) -> dict:
        """Return separate K and V quant type lists and boundary-layer support.

        Returns a dict with three fields:
            k: list[str] of valid -ctk flag values for this backend
            v: list[str] of valid -ctv flag values for this backend
            boundary: bool, True if the backend can keep first/last N layers at fp16
                      while the middle layers use a different type

        Probe is best-effort, any network or parse error silently returns the
        safe default {k: ["fp16"], v: ["fp16"], boundary: False}. Image-gen
        backends return empty lists because KV quant is not applicable to
        diffusion pipelines.

        When a backend (e.g. a future vLLM build with TurboQuant merged, or
        TheTom/llama-cpp-turboquant exposed as a llama.cpp-compat worker)
        starts reporting a richer surface, it appears here and flows up to
        the cluster-wide union without any other code changes.

        Per research (Ziskind empirical, NexusQuant llama.cpp#21591), the
        correct shape is asymmetric: keys need more bits than values. A
        single flat list cannot express this because the safe K quants and
        safe V quants are different sets.

        TODO: once a backend actually ships a /v1/kv-quant-options or
        equivalent endpoint, replace the static stubs below with a live
        probe. Track in #144.
        """
        try:
            if backend_type == "sd-cpp":
                # Image-gen backends, KV quant is not applicable.
                return {"k": [], "v": [], "boundary": False}
            # All current real backends return the default until one of them
            # starts exposing a capability endpoint. The worker merely
            # advertises what the backend says it can do.
            return {"k": ["fp16"], "v": ["fp16"], "boundary": False}
        except Exception:
            return {"k": ["fp16"], "v": ["fp16"], "boundary": False}

    async def _probe_loaded_models(
        self, client: httpx.AsyncClient, backend_type: str, base_url: str
    ) -> list[dict]:
        """Ask a backend which of its models are *currently in memory* (not
        merely downloaded and available). Used to populate the 'Loaded
        Models' widget in the Activity app so it reflects real NPU/GPU
        residency, not the full catalog of pulled-but-idle models.

        Returns empty list on any failure -- loaded-state is a best-effort
        signal and should never break heartbeat.
        """
        try:
            if backend_type in ("rkllama", "ollama", "hailo-ollama"):
                resp = await client.get(f"{base_url}/api/ps")
                if resp.status_code != 200:
                    return []
                data = resp.json()
                return [
                    {
                        "name": m.get("model") or m.get("name", ""),
                        "size_mb": (m.get("size") or 0) // 1_000_000,
                    }
                    for m in data.get("models", [])
                ]
            # Other backend types don't expose an "in memory" state yet --
            # llama-cpp serves one model per process, vLLM similar, etc.
            # For those, "available" == "loaded" so the normal /v1/models
            # list is correct. Return [] here and let the caller fall back
            # to available-models.
            return []
        except Exception:
            return []

    async def _probe_models(
        self, client: httpx.AsyncClient, backend_type: str, base_url: str
    ) -> list[dict] | None:
        """Ask a backend what models it has loaded. Returns None if the
        backend isn't reachable (not running on this host)."""
        try:
            if backend_type in ("rkllama", "ollama", "hailo-ollama"):
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code != 200:
                    return None
                data = resp.json()
                return [
                    {
                        "name": m.get("model") or m.get("name", ""),
                        "size_mb": (m.get("size") or 0) // 1_000_000,
                    }
                    for m in data.get("models", [])
                ]
            if backend_type == "sd-cpp":
                resp = await client.get(f"{base_url}/sdapi/v1/sd-models")
                if resp.status_code != 200:
                    return None
                return [
                    {"name": m.get("title") or m.get("model_name") or "", "size_mb": 0}
                    for m in (resp.json() if isinstance(resp.json(), list) else [])
                ]
            # llama-cpp / vllm, OpenAI compat /v1/models
            resp = await client.get(f"{base_url}/v1/models")
            if resp.status_code != 200:
                return None
            data = resp.json()
            return [
                {"name": m.get("id", ""), "size_mb": 0}
                for m in data.get("data", [])
            ]
        except Exception:
            return None

    def detect_kv_quant_support(self, backends: list[dict]) -> dict:
        """Aggregate KV cache K/V quant support across all detected backends.

        Returns a dict with four fields:
            k: sorted list[str] of supported -ctk values across all LLM backends
            v: sorted list[str] of supported -ctv values across all LLM backends
            boundary: bool, True if ANY backend supports boundary-layer protect
            legacy: sorted list[str] union of k and v (backwards compat)

        Image-gen backends return empty dicts from the probe and are skipped.
        All LLM-capable backends contribute at minimum {"k": ["fp16"], "v": ["fp16"]}.
        """
        k_types: set[str] = set()
        v_types: set[str] = set()
        boundary = False
        for b in backends:
            per_backend = b.get("kv_quant_support")
            if not per_backend:
                continue
            if isinstance(per_backend, dict):
                k_types.update(per_backend.get("k") or [])
                v_types.update(per_backend.get("v") or [])
                boundary = boundary or bool(per_backend.get("boundary"))
            elif isinstance(per_backend, list):
                # Legacy shape: flat list. Apply to both K and V.
                k_types.update(per_backend)
                v_types.update(per_backend)
        # Always include fp16 as the baseline so protocol stays compatible
        # with consumers that expect at least one entry.
        k_types.add("fp16")
        v_types.add("fp16")
        return {
            "k": sorted(k_types),
            "v": sorted(v_types),
            "boundary": boundary,
            "legacy": sorted(k_types | v_types),
        }

    def get_container_runtime(self) -> str | None:
        """Detect available container runtime (docker or podman). Returns None if neither found."""
        import shutil
        if shutil.which("docker"):
            return "docker"
        if shutil.which("podman"):
            return "podman"
        return None

    def supports_streaming(self) -> bool:
        """Return True if a container runtime is available for streaming apps."""
        return self.get_container_runtime() is not None

    def detect_capabilities(self, backends: list[dict]) -> list[str]:
        """Union of capabilities across all detected backends.

        Backend-driven: each backend contributes its own advertised
        capability set. Modern detect_backends() attaches the live set
        from BACKEND_CAPABILITIES on probe; a caller passing a legacy
        shape (only ``type`` on each dict) still works because we fall
        back to BACKEND_CAPABILITIES by type. Streaming is added if a
        container runtime is present.
        """
        from tinyagentos.scheduler.backend_catalog import BACKEND_CAPABILITIES

        caps: set[str] = set()
        for b in backends:
            declared = b.get("capabilities")
            if declared:
                caps.update(declared)
                continue
            btype = b.get("type")
            if btype:
                caps.update(BACKEND_CAPABILITIES.get(btype, set()))
        if self.supports_streaming():
            caps.add("app-streaming")
        return sorted(caps)

    def _advertised_url(self, backends: list[dict]) -> str:
        """The URL the controller reaches this worker on: the pinned
        advertise_url, else TAOS_ADVERTISE_IP (+ worker_port), else the first
        live loopback backend (the legacy fallback the task router relies on
        for a co-located worker), else get_worker_url(). Never a LAN backend
        from TAOS_EXTRA_BACKENDS: that is another machine, and worker-control
        traffic (with its bearer token) would go there."""
        if self.advertise_url:
            return self.advertise_url
        adv_ip = os.environ.get("TAOS_ADVERTISE_IP", "").strip()
        if adv_ip:
            return f"http://{adv_ip}:{self.worker_port}" if self.worker_port else f"http://{adv_ip}"
        for b in backends:
            url = b.get("url")
            if url and _candidate_key(b.get("type", ""), url)[2] == "loopback":
                return url
        return self.get_worker_url()

    def get_worker_url(self) -> str:
        """Get this worker's reachable URL."""
        # Try to get LAN IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            ip = "127.0.0.1"
        return f"http://{ip}:{self.worker_port}" if self.worker_port else f"http://{ip}"

    async def register(self) -> "bool | int":
        """Register with the controller (signed with HMAC key if paired).

        Returns:
            True    -- registered successfully
            False   -- generic failure (network error, non-401, etc.)
            401     -- controller rejected the signing key with a pairing
                       error (worker_not_paired or bad_signature); the
                       caller should enter the needs-re-pair state instead
                       of retrying register every 5s.
        """
        from tinyagentos.hardware import detect_hardware
        from dataclasses import asdict
        import json as _json

        # Re-read the key from disk on every attempt so that running the pair
        # CLI (which persists a new key) recovers the agent without a restart.
        from tinyagentos.worker.pairing import load_signing_key
        self._signing_key = load_signing_key(self._state_dir)

        if self._signing_key is None:
            logger.error(
                "worker not paired: no signing key at %s; "
                "run `python -m tinyagentos.worker.pair %s --name %s "
                "--state-dir %s` to pair this worker",
                self._state_dir,
                self.controller_url,
                self.name,
                self._state_dir,
            )
            return False

        hw = detect_hardware()
        hw_dict = asdict(hw)
        platform_name = platform.system().lower()
        backends = await self.detect_backends()
        caps = sorted(set(self.detect_capabilities(backends)) | set(self.extra_capabilities))
        kv_quant = self.detect_kv_quant_support(backends)
        resources = _collect_resources(backends, _gpu_type(hw_dict), platform_name)

        # Use pinned advertise_url if provided; otherwise infer from backends or LAN IP.
        # TAOS_ADVERTISE_IP is set by the worker-LXC installer: inside the LXC the
        # only locally-detectable address is the NAT'd incusbr0 IP, which the
        # controller cannot reach. The reachable address is the bare host's LAN IP
        # (DNAT'd to the LXC), so honour it for both the advertised URL and the
        # host_lan_ip used to match the incus remote to this worker.
        adv_ip = os.environ.get("TAOS_ADVERTISE_IP", "").strip()
        worker_url = self._advertised_url(backends)

        payload = {
            "name": self.name,
            "url": worker_url,
            "host_lan_ip": adv_ip or _detect_lan_ip(self.controller_url),
            "hardware": hw_dict,
            "backends": backends,
            "capabilities": caps,
            "platform": platform_name,
            "models": [],
            "resources": resources,
            "kv_cache_quant_support": kv_quant.get("legacy", ["fp16"]),
            "kv_cache_quant_k_support": kv_quant.get("k", ["fp16"]),
            "kv_cache_quant_v_support": kv_quant.get("v", ["fp16"]),
            "kv_cache_quant_boundary_layer_protect": bool(kv_quant.get("boundary", False)),
            "generation": self._generation,
        }
        # Forward the storage-backup marker once. Controller materialises
        # a workspace text file + a notification so the user sees the
        # rename next time they open taOS.
        backup = _read_storage_backup_marker()
        if backup:
            payload["pending_storage_backup"] = backup

        try:
            from tinyagentos.worker.pairing import sign_request_headers
            path = "/api/cluster/workers"
            body = _json.dumps(payload).encode()
            auth_headers = sign_request_headers(self._signing_key, self.name, "POST", path, body)
            auth_headers["content-type"] = "application/json"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.controller_url}{path}",
                    content=body,
                    headers=auth_headers,
                )
                if _is_repair_rejection(resp):
                    return _NEEDS_REPAIR
                # taOS #...: handle stale_generation 409 by adopting the echoed
                # generation monotonically (only if higher than current).
                if resp.status_code == 409:
                    try:
                        error_body = resp.json()
                        if (
                            error_body.get("error") == "stale_generation"
                            and isinstance(error_body.get("generation"), int)
                        ):
                            echoed_gen = error_body["generation"]
                            if self._generation is None or echoed_gen > self._generation:
                                self._generation = echoed_gen
                                logger.info(
                                    f"Adopted controller generation {echoed_gen} from stale_generation 409"
                                )
                    except Exception:  # noqa: BLE001
                        pass
                    return False
                resp.raise_for_status()
                self._registered = True
                # Capture the controller's current generation from the response
                try:
                    resp_gen = resp.json().get("generation")
                    if resp_gen is not None:
                        self._generation = resp_gen
                    else:
                        logger.warning(
                            "register response carried no generation echo - "
                            "split-brain layer-2 protection stays disarmed"
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"could not read generation from register response: {exc}")
                logger.info(f"Registered with controller as '{self.name}'")
                if backup:
                    _delete_storage_backup_marker()
                return True
        except Exception as e:
            logger.error(f"Failed to register: {e}")
            return False

    async def heartbeat(self, status: str | None = None, drain_reason: str | None = None) -> int:
        """Send heartbeat to controller with live backend catalog.

        Backend-driven: the heartbeat carries a fresh probe of every
        detected backend, not a cached snapshot. This lets the controller
        aggregate per-worker catalogs into a cluster-wide view that
        reflects what's actually loaded right now across the mesh.

        Worker-initiated state transitions (taOS #890 C2): when ``status``
        is set to ``"update-available"`` or ``"draining"``, the controller
        heeds the worker's self-reported status instead of forcing it back
        to ``"online"``. This inverts the classic controller-push drain
        into a worker-pull drain: the worker detects an update, reports
        its readiness to drain, waits for inflight work to complete, then
        proceeds to self-install.

        Returns the HTTP status code from the controller, or 0 on
        connection failure / timeout. The caller uses this to detect
        the 404 case (controller restarted and forgot about us) and
        trigger a re-registration. A 401 with code worker_not_paired or
        bad_signature indicates the key the controller holds no longer
        matches (migration case); the run loop enters the needs-re-pair
        state on this code.
        """
        from tinyagentos.cluster.worker_capacity import capacity_snapshot, gpu_vram_snapshot
        import json as _json

        # Re-read the key from disk on every attempt so that running the pair
        # CLI (which persists a new key) recovers the agent without a restart.
        from tinyagentos.worker.pairing import load_signing_key
        self._signing_key = load_signing_key(self._state_dir)

        if self._signing_key is None:
            logger.error(
                "worker not paired: no signing key at %s; "
                "run `python -m tinyagentos.worker.pair %s --name %s "
                "--state-dir %s` to pair this worker",
                self._state_dir,
                self.controller_url,
                self.name,
                self._state_dir,
            )
            return 0

        from tinyagentos.hardware import detect_hardware
        from dataclasses import asdict

        try:
            from tinyagentos.worker.pairing import sign_request_headers
            # Re-resolve host_lan_ip, url, and hardware on every heartbeat
            # so the controller stays in sync with container reality after
            # DHCP moves or LXC restarts (taOS #1538).
            # Note: the function call asdict(detect_hardware()) remains inside
            # the try so a probe failure degrades gracefully (heartbeat skips
            # this update) rather than crashing the run loop.
            load = psutil.cpu_percent() / 100.0
            backends = await self.detect_backends()
            caps = sorted(set(self.detect_capabilities(backends)) | set(self.extra_capabilities))
            live_hardware = asdict(detect_hardware())
            kv_quant = self.detect_kv_quant_support(backends)
            resources = _collect_resources(
                backends, _gpu_type(live_hardware), platform.system().lower()
            )
            snap = capacity_snapshot()
            vram_sample = gpu_vram_snapshot()
            vram_sampled_age_ms = None
            if vram_sample:
                vram = vram_sample
                # vram_sampled_age_ms = age since the sample was taken (for transit bug fix)
                # If sample was taken at t0 and arrives at t2, age = (t2 - t0) * 1000
                vram_sampled_age_ms = int((time.time() - vram_sample.get('sampled_at', time.time())) * 1000)
                # Clamp negative ages to 0
                if vram_sampled_age_ms < 0:
                    vram_sampled_age_ms = 0
            else:
                vram = None
                vram_sampled_age_ms = None
            adv_ip = os.environ.get("TAOS_ADVERTISE_IP", "").strip()
            live_url = self._advertised_url(backends)
            live_host_lan_ip = adv_ip or _detect_lan_ip(self.controller_url)
            path = "/api/cluster/heartbeat"
            payload = {
                "name": self.name,
                "load": load,
                "backends": backends,
                "capabilities": caps,
                "resources": resources,
                "kv_cache_quant_support": kv_quant.get("legacy", ["fp16"]),
                "kv_cache_quant_k_support": kv_quant.get("k", ["fp16"]),
                "kv_cache_quant_v_support": kv_quant.get("v", ["fp16"]),
                "kv_cache_quant_boundary_layer_protect": bool(kv_quant.get("boundary", False)),
                "storage_cap_bytes": snap["storage_cap_bytes"],
                "storage_used_bytes": snap["storage_used_bytes"],
                "bytes_deduped_total": snap["bytes_deduped_total"],
                # None (not 0) when no VRAM probe is available so the
                # controller can tell "unknown" apart from "no VRAM free".
                "free_vram_mb": vram["free_vram_mb"] if vram else None,
                "used_vram_mb": vram["used_vram_mb"] if vram else None,
                "vram_sampled_age_ms": vram_sampled_age_ms,
                # Registration-drift refresh (taOS #1538): send live
                # host_lan_ip, url, and hardware on every heartbeat
                # so IP/subnet moves are reflected immediately.
                "host_lan_ip": live_host_lan_ip,
                "url": live_url,
                "hardware": live_hardware,
                # Worker-initiated state transitions (taOS #890 C2).
                "status": status,
                "drain_reason": drain_reason,
                "generation": self._generation,
            }
            # Attach worker update-check state so the controller surfaces it
            # in the Resource Manager / cluster view.
            if self._update_service is not None:
                payload["update_state"] = self._update_service.get_state()
            body = _json.dumps(payload).encode()
            auth_headers = sign_request_headers(self._signing_key, self.name, "POST", path, body)
            auth_headers["content-type"] = "application/json"
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self.controller_url}{path}",
                    content=body,
                    headers=auth_headers,
                )
                # Capture the controller's current generation from the response
                # only on successful responses
                if resp.status_code == 200:
                    try:
                        resp_json = resp.json()
                        gen = resp_json.get("generation")
                        if gen is not None:
                            self._generation = gen
                        elif self._generation is not None:
                            logger.warning(
                                "heartbeat response stopped echoing generation - "
                                "split-brain layer-2 protection may be degraded"
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"could not read generation from heartbeat response: {exc}")
                return resp.status_code
        except Exception as exc:  # noqa: BLE001
            # Log before swallowing: a payload-build bug (not just a network
            # blip) would otherwise drop the worker offline with zero
            # diagnostics, masquerading as controller unreachability.
            logger.warning("heartbeat send failed: %s: %s", type(exc).__name__, exc)
            return 0

    # ── Worker-initiated drain (taOS #890 C2) ──────────────────────────

    async def report_update_available(self, reason: str = "update") -> int:
        """Report that an update is available but the worker is still serving.

        Transitions the worker to ``"update-available"`` status on the
        controller. The worker continues accepting tasks; this is a
        precursor signal that a drain will follow when the worker is
        ready.
        """
        logger.info("worker '%s': reporting update available (reason=%s)", self.name, reason)
        self._lifecycle_status = "update-available"
        self._lifecycle_reason = reason
        return await self.heartbeat(status="update-available", drain_reason=reason)

    async def initiate_self_drain(self, reason: str = "update") -> int:
        """Initiate a worker-initiated graceful drain.

        Sends a heartbeat with ``status="draining"`` to the controller,
        which stops routing new tasks to this worker while letting
        in-flight leases complete. The monitor loop auto-completes the
        drain when all leases are released.

        Returns the HTTP status code from the controller (200 on success).
        """
        logger.info("worker '%s': initiating self-drain (reason=%s)", self.name, reason)
        self._lifecycle_status = "draining"
        self._lifecycle_reason = reason
        return await self.heartbeat(status="draining", drain_reason=reason)

    async def notify_drain_complete(self) -> int:
        """Notify the controller that the worker's drain is complete.

        After in-flight work finishes, the worker sends one final
        heartbeat with status="updating" to signal readiness for
        the update. The controller can then proceed with the update
        deploy.

        Returns the HTTP status code from the controller.
        """
        logger.info("worker '%s': drain complete, ready for update", self.name)
        self._lifecycle_status = "updating"
        self._lifecycle_reason = "drain-complete"
        return await self.heartbeat(status="updating")

    def _log_repair_instruction(self) -> None:
        logger.error(
            "worker '%s' needs re-pairing: the controller rejected the signing key "
            "(controller upgraded or pairing store reset). "
            "Run: python -m tinyagentos.worker.pair %s --name %s",
            self.name,
            self.controller_url,
            self.name,
        )

    async def run(self):
        """Main worker loop, register, heartbeat, re-register on loss.

        The controller's in-memory cluster registry is wiped on every
        controller restart. When that happens our heartbeats start
        coming back as 404 'Worker not registered'. Treat that as a
        signal to re-register and resume, without it, every controller
        restart leaves the cluster view empty until the worker is
        manually restarted.

        When the controller rejects the signing key with a pairing error
        (401 worker_not_paired / bad_signature), the worker enters a
        needs-re-pair state: logs a clear re-pair instruction once on
        entry (re-logged every ~5 min), backs off to a longer interval,
        and stops hammering register. It recovers automatically if the
        pair CLI is run and a new key is persisted -- no agent restart
        needed.
        """
        self._running = True
        _in_repair = False
        _last_repair_log: float = 0.0

        # Start the worker update-check service (background version polling).
        from tinyagentos.worker.update_check import WorkerUpdateService
        self._update_service = WorkerUpdateService(
            state_dir=self._state_dir,
            worker_name=self.name,
        )
        await self._update_service.start()

        try:
            while self._running:
                # Register if we aren't (yet, or any more).
                if not self._registered:
                    result = await self.register()
                    if result is True:
                        logger.info(f"worker '{self.name}' registered with {self.controller_url}")
                        _in_repair = False
                        continue
                    if result == _NEEDS_REPAIR:
                        # Controller rejected our key -- enter needs-re-pair state.
                        now = time.monotonic()
                        if not _in_repair or (now - _last_repair_log) >= _REPAIR_LOG_INTERVAL:
                            self._log_repair_instruction()
                            _last_repair_log = now
                        _in_repair = True
                        await asyncio.sleep(_REPAIR_INTERVAL)
                        continue
                    # Generic registration failure -- short retry.
                    _in_repair = False
                    await asyncio.sleep(5)
                    continue

                # Include any persisted lifecycle status (taOS #890 C2) so that
                # a controller restart doesn't silently revert a
                # draining/updating worker back to "online" after re-registration.
                status = await self.heartbeat(
                    status=self._lifecycle_status,
                    drain_reason=self._lifecycle_reason,
                )
                if status == 404:
                    # Controller has forgotten about us (restart, manual
                    # deregister, etc). Drop our registered state and the
                    # next loop iteration will re-register.
                    logger.warning(
                        f"controller returned 404 on heartbeat, re-registering '{self.name}'"
                    )
                    self._registered = False
                elif status == 401:
                    # Controller rejected our signing key -- enter needs-re-pair state.
                    self._registered = False
                    now = time.monotonic()
                    if not _in_repair or (now - _last_repair_log) >= _REPAIR_LOG_INTERVAL:
                        self._log_repair_instruction()
                        _last_repair_log = now
                    _in_repair = True
                    await asyncio.sleep(_REPAIR_INTERVAL)
                    continue
                elif status == 0:
                    # Network / DNS / controller-down. Don't drop the
                    # registered flag yet; the controller may still know
                    # us when it comes back. Just retry on next tick.
                    pass
                await asyncio.sleep(5)
        finally:
            if self._update_service is not None:
                await self._update_service.stop()

    def stop(self):
        self._running = False
        if self._update_service is not None:
            self._update_service._stop_event.set()
