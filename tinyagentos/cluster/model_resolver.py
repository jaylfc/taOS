"""Resolve where a model lives across the cluster.

This is the route-only stepping stone for cross-worker deploy routing
(task #176). The deploy endpoint calls :func:`find_model_hosts` once
with the requested model id and a snapshot of:

- the controller's local BackendCatalog model list (what the controller
  itself can serve right now)
- the ClusterManager's aggregated worker catalog (what remote workers
  report via heartbeat)
- an optional flat list of cloud-provider model ids (openai / anthropic
  / etc) so LiteLLM-proxied models resolve as ``cloud`` and fall through
  to the unchanged controller-local deploy path.

The helper is intentionally small and synchronous: it does not hit the
network or the disk. All inputs are already-cached in-memory state.

Phase 1.5 (network model placement over bittorrent) will grow this into
a real placement planner. For now it only answers the question
"where is this model right now?".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelLocation:
    """Result of :func:`find_model_hosts`.

    Attributes:
        kind: One of ``controller`` | ``worker`` | ``cloud`` | ``not_found``
            | ``downloaded_backend_down``.
        hosts: Worker names that report the model (empty unless
            ``kind == "worker"``).
        canonical_host: The worker chosen when multiple have the model.
            Stubbed as alphabetical pick — Phase 1.5 will consider load
            and hardware.
        backend_id: Set only when ``kind == "downloaded_backend_down"`` —
            the manifest-declared backend (e.g. ``"rkllama"``) that this
            model needs, confirmed not running right now (see
            :func:`_check_downloaded_backend_down`).
    """

    kind: str
    hosts: list[str] = field(default_factory=list)
    canonical_host: str | None = None
    backend_id: str | None = None


def _normalise(model_id: str) -> str:
    """Lowercase + strip common version suffixes for loose matching.

    The controller's registry uses manifest ids like ``qwen2.5-7b`` while
    a worker's live backend may report ``qwen2.5:7b`` (Ollama) or
    ``qwen2.5-7b-instruct-q4_k_m.gguf`` (llama.cpp). We match on a
    normalised form so "the user picked qwen2.5-7b in the wizard" still
    finds the Fedora copy even if Fedora's Ollama calls it
    ``qwen2.5:7b``.
    """
    return (
        model_id.strip().lower()
        .replace(":", "-")
        .replace("_", "-")
    )


def _ext_match(longer: str, shorter: str) -> bool:
    """True if ``longer`` is ``shorter`` plus a file extension.

    We only allow the ``.`` separator to act as a variant boundary when the
    character after the dot is a letter, so ``qwen2.5-7b.gguf`` still
    matches ``qwen2.5-7b`` but ``qwen3.5-4b`` does NOT match ``qwen3``
    (the ``.5`` is a version, not an extension).
    """
    if not longer.startswith(shorter + "."):
        return False
    tail = longer[len(shorter) + 1 :]
    return bool(tail) and tail[0].isalpha()


def _model_matches(target: str, candidate: str) -> bool:
    """True if ``candidate`` is the same model as ``target``.

    Loose prefix match on the normalised form so variant-suffix backends
    (``-q4_k_m``, ``-instruct``, ``.gguf``, ``.safetensors``) still
    resolve. The target is the user's pick from the wizard; the candidate
    is whatever the backend reports. The ``.`` separator only matches
    when the following character is a letter, so ``qwen3`` cannot be
    treated as a shorter alias for ``qwen3.5``.
    """
    if not target or not candidate:
        return False
    t = _normalise(target)
    c = _normalise(candidate)
    if t == c:
        return True
    # Allow backend-reported ids that carry a variant suffix or extension
    if c.startswith(t + "-"):
        return True
    if _ext_match(c, t):
        return True
    # Allow wizard-picked ids that are a shorter alias of the backend id
    if t.startswith(c + "-"):
        return True
    if _ext_match(t, c):
        return True
    return False


def find_model_hosts(
    model_id: str,
    cluster_state,
    local_models: list[dict] | None = None,
    cloud_models: list[str] | None = None,
) -> ModelLocation:
    """Locate a model across the cluster.

    Args:
        model_id: The model id the user picked in the deploy wizard.
        cluster_state: Either a :class:`ClusterManager` instance (we
            call :meth:`get_workers`) or an already-collected iterable of
            worker objects / dicts. Each worker must expose either a
            ``backends`` list (preferred; each backend has ``models``)
            or a flat ``models`` list of strings.
        local_models: Flat list of loaded-model dicts from the
            controller's own BackendCatalog (``catalog.all_models()``).
            Each dict should carry ``name`` or ``id``.
        cloud_models: Optional flat list of cloud-provider model ids
            (openai / anthropic / litellm aliases). Used to distinguish
            ``cloud`` from ``not_found`` when nothing on the mesh has
            the model.

    Returns:
        A :class:`ModelLocation` with ``kind`` and (for worker-hosted
        models) the list of worker names that have it.
    """
    if not model_id:
        return ModelLocation(kind="not_found")

    # 1. Controller-local? Live BackendCatalog wins — if the controller
    #    itself has the model loaded we always stay on the controller
    #    and leave the existing deploy path untouched.
    for m in local_models or []:
        name = m.get("name") or m.get("id") or ""
        if _model_matches(model_id, name):
            return ModelLocation(kind="controller")

    # 2. On any online worker? Walk the aggregated cluster catalog.
    if hasattr(cluster_state, "get_workers"):
        workers = cluster_state.get_workers()
    else:
        workers = list(cluster_state or [])

    hosts: list[str] = []
    for w in workers:
        status = getattr(w, "status", None) or (w.get("status") if isinstance(w, dict) else None)
        if status and status != "online":
            continue
        name = getattr(w, "name", None) or (w.get("name") if isinstance(w, dict) else None)
        if not name:
            continue

        # Prefer the rich backends list (live per-backend catalog)
        backends = getattr(w, "backends", None)
        if backends is None and isinstance(w, dict):
            backends = w.get("backends")
        matched = False
        for b in backends or []:
            for bm in b.get("models") or []:
                bm_name = bm.get("name") if isinstance(bm, dict) else str(bm)
                if _model_matches(model_id, bm_name or ""):
                    matched = True
                    break
            if matched:
                break

        # Fallback to the flat worker.models list (legacy heartbeats)
        if not matched:
            flat = getattr(w, "models", None)
            if flat is None and isinstance(w, dict):
                flat = w.get("models")
            for fm in flat or []:
                fm_name = fm if isinstance(fm, str) else (fm.get("name") if isinstance(fm, dict) else "")
                if _model_matches(model_id, fm_name or ""):
                    matched = True
                    break

        if matched and name not in hosts:
            hosts.append(name)

    if hosts:
        hosts_sorted = sorted(hosts)
        return ModelLocation(
            kind="worker",
            hosts=hosts_sorted,
            canonical_host=hosts_sorted[0],
        )

    # 3. Cloud? Only if nothing on the mesh has it.
    for cm in cloud_models or []:
        if _model_matches(model_id, cm):
            return ModelLocation(kind="cloud")

    return ModelLocation(kind="not_found")


# Maps a manifest's ``requires.backends[].id`` to the probe that answers
# "is this backend actually running on this host right now?" and to the
# human-facing copy used in actionable error messages. Reuses the exact
# probes the Setup checklist already relies on (#1535/#1597/#1598) so a
# model resolver failure and the checklist agree on backend liveness.
_BACKEND_LABELS: dict[str, str] = {
    "rkllama": "rkllama NPU backend",
    "rk-llama-cpp": "rk-llama.cpp NPU backend",
    "llama-cpp": "llama.cpp backend",
}

_BACKEND_INSTALL_HINTS: dict[str, str] = {
    "rkllama": "Install/start it from Setup > Install NPU backend",
    "rk-llama-cpp": "Install/start it from Setup > Install NPU backend",
    "llama-cpp": "Install/start it from Setup > Install llama.cpp server",
}


def _backend_is_running(backend_id: str) -> bool | None:
    """True/False if we can positively probe *backend_id*, else None.

    None means "we don't know how to check this backend" — callers must
    NOT report it as down on an unknown answer, only on a confirmed-False
    probe. Never raises: an import or probe error is treated as unknown.
    """
    try:
        if backend_id == "rkllama":
            from tinyagentos.installers.rkllama_installer import rkllama_is_running

            return rkllama_is_running()
        if backend_id == "rk-llama-cpp":
            from tinyagentos.installers.rkllamacpp_installer import rkllamacpp_is_running

            return rkllamacpp_is_running()
        if backend_id == "llama-cpp":
            from tinyagentos.installers.llamacpp_installer import llamacpp_is_running

            return llamacpp_is_running()
    except Exception:  # noqa: BLE001 — probe is best-effort, never break resolution
        return None
    return None


def _find_model_manifest(registry, model_id: str):
    """Best-effort manifest lookup for *model_id* against the app registry.

    Tries an exact id match first (the common case — manifest ids like
    ``qwen2.5-3b-rkllm`` are exactly what wizards/pickers pass through),
    then falls back to the same loose :func:`_model_matches` used
    elsewhere in this module. Returns ``None`` on any error or when the
    registry doesn't know this model at all.
    """
    if registry is None:
        return None
    try:
        manifest = registry.get(model_id)
        if manifest is not None and manifest.type == "model":
            return manifest
        for m in registry.list_available(type_filter="model"):
            if _model_matches(model_id, m.id):
                return m
    except Exception:  # noqa: BLE001
        return None
    return None


def _required_backend_ids(manifest) -> list[str]:
    """Unique ``requires.backends[].id`` values declared across all variants.

    Variants come straight from parsed manifest YAML (always dicts today),
    but this runs on the hot HTTP resolution path, so a non-dict variant is
    skipped rather than allowed to raise and turn a clean 4xx into a 500.
    """
    ids: list[str] = []
    for v in getattr(manifest, "variants", None) or []:
        if not isinstance(v, dict):
            continue
        for b in (v.get("requires") or {}).get("backends") or []:
            bid = b.get("id") if isinstance(b, dict) else None
            if bid and bid not in ids:
                ids.append(bid)
    return ids


def _check_downloaded_backend_down(request, model_id: str) -> ModelLocation | None:
    """When *model_id* resolved to ``not_found``, check whether it is a
    known, downloaded model whose required backend we can positively
    confirm is not running — the real cause behind #1599/#1600 (an
    rkllama-backed model that shows as downloaded and selectable but the
    rkllama server itself isn't up right now).

    Returns a ``downloaded_backend_down`` :class:`ModelLocation` only when
    every backend the manifest requires is confirmed down. Returns
    ``None`` (defer to the generic "not found" message) when the manifest
    is unknown, declares no backend requirement, or we can't positively
    confirm any required backend is down (never claim a backend is down
    on an inconclusive probe).
    """
    registry = getattr(request.app.state, "registry", None)
    manifest = _find_model_manifest(registry, model_id)
    if manifest is None:
        return None
    backend_ids = _required_backend_ids(manifest)
    if not backend_ids:
        return None
    down: list[str] = []
    for backend_id in backend_ids:
        running = _backend_is_running(backend_id)
        if running is None:
            return None
        if not running:
            down.append(backend_id)
    if not down:
        return None
    return ModelLocation(kind="downloaded_backend_down", backend_id=down[0])


def describe_downloaded_backend_down(location: ModelLocation, model_id: str) -> str:
    """Actionable error text for a ``downloaded_backend_down`` location.

    Shared by every route that reports model reachability so the wording
    — and any future fix to it — lives in one place.
    """
    backend_id = location.backend_id or ""
    label = _BACKEND_LABELS.get(backend_id, f"{backend_id} backend" if backend_id else "backend")
    hint = _BACKEND_INSTALL_HINTS.get(backend_id, f"Install/start the {label}")
    return (
        f"model '{model_id}' is downloaded but the {label} that serves it is not "
        f"running. {hint} and try again."
    )


def collect_cloud_model_ids(config) -> list[str]:
    """Best-effort list of cloud-provider model ids advertised in config.backends.

    Cloud provider types are :data:`tinyagentos.providers.CLOUD_TYPES`. Never
    raises — on any error returns what was gathered so far.
    """
    # Lazy import to avoid any import cycle with providers.
    from tinyagentos.providers import CLOUD_TYPES  # noqa: PLC0415

    cloud_models: list[str] = []
    try:
        for b in config.backends or []:
            if b.get("type") in CLOUD_TYPES:
                for m in b.get("models") or []:
                    mid = (m.get("id") or m.get("name") or "") if isinstance(m, dict) else str(m)
                    if mid:
                        cloud_models.append(mid)
    except Exception:  # noqa: BLE001
        pass
    return cloud_models


def resolve_model_location(request, model_id: str) -> ModelLocation:
    """Resolve *model_id* against controller catalog + cluster workers + configured
    cloud providers, reading state off ``request.app.state``.

    Returns a :class:`ModelLocation`.
    """
    state = request.app.state
    cluster = getattr(state, "cluster_manager", None)
    catalog = getattr(state, "backend_catalog", None)
    local_models = catalog.all_models() if catalog is not None else []
    config = getattr(state, "config", None)
    cloud_models = collect_cloud_model_ids(config) if config is not None else []
    location = find_model_hosts(
        model_id,
        cluster_state=cluster,
        local_models=local_models,
        cloud_models=cloud_models,
    )
    if location.kind == "not_found":
        downloaded_backend_down = _check_downloaded_backend_down(request, model_id)
        if downloaded_backend_down is not None:
            return downloaded_backend_down
    return location
