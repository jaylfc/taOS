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
        kind: One of ``controller`` | ``worker`` | ``cloud`` | ``not_found``.
        hosts: Worker names that report the model (empty unless
            ``kind == "worker"``).
        canonical_host: The worker chosen when multiple have the model.
            Stubbed as alphabetical pick — Phase 1.5 will consider load
            and hardware.
    """

    kind: str
    hosts: list[str] = field(default_factory=list)
    canonical_host: str | None = None


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
    return find_model_hosts(
        model_id,
        cluster_state=cluster,
        local_models=local_models,
        cloud_models=cloud_models,
    )
