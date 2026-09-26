"""The system taOS Agent's harness: which one runs it, and its lifecycle.

Two harnesses can run the system taOS Agent:

- opencode (every host by default): a single host ``opencode serve`` used
  exclusively by the taOS agent chat endpoint, started lazily on the first
  chat request and kept alive for the process lifetime. The persistent
  session id is stored on app.state so opencode remembers the conversation.
- PicoClaw (a taOSmobile handset by default): one ``picoclaw agent`` process
  per turn, talking to the controller's own LLM gateway with a scoped key
  (see :mod:`tinyagentos.picoclaw_runtime`).

:func:`decide_framework` is the ONE place the choice is made, from
``device.class`` / ``taos_agent.framework`` in config.yaml, the detected
device class and whether the gateway is on. :func:`system_agent_framework`
reports the EFFECTIVE harness (never just the preference), and the lock
screen and ``/api/taos-agent/config`` both read it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from tinyagentos.litellm_config import get_litellm_master_key
from tinyagentos.opencode_runtime import OpenCodeServer, OpenCodeServerConfig

logger = logging.getLogger(__name__)

TAOS_OPENCODE_PORT = 4188  # local-only port for the taOS agent opencode server

FRAMEWORK_OPENCODE = OpenCodeServer.__name__.split("Server")[0].lower()  # "opencode"
FRAMEWORK_PICOCLAW = "picoclaw"
FRAMEWORK_CHOICES = ("auto", FRAMEWORK_OPENCODE, FRAMEWORK_PICOCLAW)
DEVICE_CLASS_CHOICES = ("auto", "mobile", "desktop")

#: The gateway principal the PicoClaw-run taOS Agent's key is bound to.
PICOCLAW_PRINCIPAL = "agent:taos-agent"

REASON_GATEWAY_DISABLED = "picoclaw preferred, gateway disabled, using opencode"
REASON_NO_BINARY = "picoclaw preferred, picoclaw binary not found, using opencode"


@dataclass(frozen=True)
class FrameworkDecision:
    framework: str
    """The harness that actually runs the taOS Agent."""
    preference: str
    """What the settings ask for (auto resolved against the device class)."""
    reason: str
    device_class: str
    """The effective device class: "mobile" or "desktop"."""


def _setting(value, choices: tuple[str, ...], name: str) -> str:
    if isinstance(value, str) and value.strip().lower() in choices:
        return value.strip().lower()
    if value not in (None, ""):
        logger.warning(
            "taos_agent_runtime: %s=%r is not one of %s; treating it as auto",
            name, value, "|".join(choices),
        )
    return "auto"


def decide_framework(
    *,
    framework_setting,
    device_class_setting,
    detected_device_class: str | None,
    gateway_enabled: bool,
    picoclaw_available: bool,
) -> FrameworkDecision:
    """Pick the system taOS Agent's harness. Pure: no I/O besides a warning.

    ``taos_agent.framework`` "opencode"/"picoclaw" is an operator override;
    "auto" means picoclaw on a mobile device and opencode everywhere else.
    ``device.class`` "auto" takes the detected class (hardware.py's kiosk-unit
    probe). PicoClaw needs the LLM gateway and the binary; without either,
    opencode runs and the reason says why.
    """
    dev = _setting(device_class_setting, DEVICE_CLASS_CHOICES, "device.class")
    if dev == "auto":
        device_class = "mobile" if detected_device_class == "mobile" else "desktop"
    else:
        device_class = dev
    fw = _setting(framework_setting, FRAMEWORK_CHOICES, "taos_agent.framework")
    if fw == "auto":
        preference = FRAMEWORK_PICOCLAW if device_class == "mobile" else FRAMEWORK_OPENCODE
        why = f"auto on a {device_class} device"
    else:
        preference = fw
        why = f"operator override taos_agent.framework={fw}"
    if preference == FRAMEWORK_PICOCLAW:
        if not gateway_enabled:
            return FrameworkDecision(FRAMEWORK_OPENCODE, preference, REASON_GATEWAY_DISABLED, device_class)
        if not picoclaw_available:
            return FrameworkDecision(FRAMEWORK_OPENCODE, preference, REASON_NO_BINARY, device_class)
    return FrameworkDecision(preference, preference, why, device_class)


def refresh_framework_decision(app_state) -> FrameworkDecision:
    """Decide from app_state's config, hardware profile and the gateway flag;
    store it on ``app_state.taos_agent_framework_decision`` and log it."""
    from tinyagentos import llm_gateway
    from tinyagentos.picoclaw_runtime import resolve_picoclaw_binary

    config = getattr(app_state, "config", None)
    profile = getattr(app_state, "hardware_profile", None)
    decision = decide_framework(
        framework_setting=(getattr(config, "taos_agent", None) or {}).get("framework"),
        device_class_setting=(getattr(config, "device", None) or {}).get("class"),
        detected_device_class=getattr(profile, "device_class", None),
        gateway_enabled=llm_gateway.enabled(),
        picoclaw_available=resolve_picoclaw_binary() is not None,
    )
    app_state.taos_agent_framework_decision = decision
    if decision.framework != decision.preference:
        logger.warning("taOS Agent harness: %s", decision.reason)
    else:
        logger.info("taOS Agent harness: %s (%s)", decision.framework, decision.reason)
    return decision


def system_agent_framework(app_state=None) -> str:
    """Return the framework id of the harness that RUNS the system taOS Agent.

    The effective harness, not the preference: with picoclaw preferred and
    the gateway off this says "opencode", because opencode is what runs.
    Without an app state (or before the first decision) it is opencode, the
    harness every host ran before PicoClaw existed.
    """
    decision = getattr(app_state, "taos_agent_framework_decision", None) if app_state is not None else None
    if isinstance(decision, FrameworkDecision):
        return decision.framework
    return FRAMEWORK_OPENCODE


# Safe filesystem component for opencode home directories.  Agent ids and
# LiteLLM model names can contain '/' (openai/gpt-4o) and other characters
# unsafe for a path; this collapses them to a flat slug.  Must stay in sync
# with the mint-side validator in routes/agent_model_keys.py.
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_path_component(value: str) -> str:
    """Replace characters unsafe for a filesystem path component.

    A model id like ``openai/gpt-4o`` becomes ``openai_gpt-4o`` so the
    opencode home stays flat under data_dir.  Traversal payloads like
    ``../../x`` collapse to ``.._.._x`` — harmless without real slashes.

    Appends a short hex digest so two distinct inputs that happen to slugify
    to the same string (e.g. ``openai/gpt-4o`` and literal ``openai_gpt-4o``)
    do not share a home directory and cross-contaminate conversation history.
    """
    slug = _SAFE_COMPONENT_RE.sub("_", value)
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{slug}-{digest}"


async def ensure_taos_opencode_server(app_state, model: str) -> OpenCodeServer:
    """Lazily create and start the taOS agent opencode server.

    Stores the server on ``app_state.taos_opencode_server``.  If the model
    changed since last start the old server is stopped and a new one is
    created so the LiteLLM provider config and key scope track the chosen model.

    The key is scoped to the full ``permitted_models`` set read from the
    ``taos_agent`` desktop_settings namespace (falls back to ``[model]``).

    If the server was created while LiteLLM was not yet ready (born degraded),
    it is torn down and rebuilt transparently on the next call once the proxy
    is running so callers never need to know about the race.

    Returns the running :class:`~tinyagentos.opencode_runtime.OpenCodeServer`.
    """
    lock = getattr(app_state, "taos_opencode_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app_state.taos_opencode_lock = lock

    # The lock is held from the server-cache lookup through ensure_running():
    # the existing-server check and the start are separated by several awaits
    # (stopping other servers, prefs read, key mint), so without it two
    # concurrent requests both observe `existing is None` and both start a
    # server on the shared TAOS_OPENCODE_PORT.
    async with lock:
        return await _ensure_taos_opencode_server_locked(app_state, model)


async def _ensure_taos_opencode_server_locked(app_state, model: str) -> OpenCodeServer:
    # Generate a stable per-process password once.
    if not getattr(app_state, "taos_opencode_password", None):
        app_state.taos_opencode_password = secrets.token_hex(16)

    # Per-agent server cache: each consented agent_id (or LLM model, for the
    # desktop path) gets its OWN server keyed by `model`, so concurrent
    # requests for different agents do not churn a shared singleton and race.
    servers = getattr(app_state, "taos_opencode_servers", None)
    if servers is None:
        servers = {}
        app_state.taos_opencode_servers = servers
    sessions = getattr(app_state, "taos_opencode_sessions", None)
    if sessions is None:
        sessions = {}
        app_state.taos_opencode_sessions = sessions

    existing: OpenCodeServer | None = servers.get(model)
    born_degraded = getattr(app_state, "taos_opencode_born_degraded", None)
    if born_degraded is None:
        born_degraded = {}
        app_state.taos_opencode_born_degraded = born_degraded


    # Self-heal: if the cached server was born before LiteLLM was ready and
    # LiteLLM is now running, tear down the degraded server and fall through
    # to a fresh build so the key re-scope and model_ids are applied properly.
    if existing is not None and born_degraded.get(model, False):
        llm_proxy_check = getattr(app_state, "llm_proxy", None)
        if llm_proxy_check is not None and llm_proxy_check.is_running():
            logger.info(
                "taos_agent_runtime: LiteLLM now ready; rebuilding taOS opencode server "
                "for %s that was born degraded", model,
            )
            try:
                await existing.stop()
            except Exception:
                logger.debug("taos_agent_runtime: error stopping degraded server", exc_info=True)
            servers.pop(model, None)
            sessions.pop(model, None)
            born_degraded[model] = False
            existing = None

    if existing is None:
        # Stop-on-model-change: all per-model servers share TAOS_OPENCODE_PORT,
        # so only one can bind at a time. Stop any server for a different model
        # before starting the new one.  Home directories are NOT removed on
        # model switch — the per-model home is the conversation store, and
        # deleting it would discard history and force a multi-minute SQLite
        # migration on the next start (the 180s deadline at ensure_running
        # exists to absorb exactly that one-time migration).
        data_dir = getattr(app_state, "data_dir", None)
        for other_model, other_server in list(servers.items()):
            if other_model != model and other_server is not None:
                logger.info(
                    "taos_agent_runtime: model changed (%s -> %s); stopping opencode server for %s",
                    other_model, model, other_model,
                )
                try:
                    await other_server.stop()
                except Exception:
                    logger.debug("taos_agent_runtime: error stopping old server", exc_info=True)
                servers.pop(other_model, None)
                sessions.pop(other_model, None)
                born_degraded.pop(other_model, None)
        # Clear the legacy session id so the desktop chat path does not feed
        # a stale session from a now-stopped model to the new server.
        app_state.taos_opencode_session_id = None

        # Read the taos_agent prefs once: the permitted set (to scope the key)
        # and a persisted own-key (so we reuse it instead of re-minting).
        permitted_models: list[str] = [model]
        stored_key: str | None = None
        prefs: dict = {}
        desktop_settings = getattr(app_state, "desktop_settings", None)
        if desktop_settings is not None:
            try:
                prefs = await desktop_settings.get_preference("user", "taos_agent") or {}
                stored = prefs.get("permitted_models", [])
                if stored:
                    # Always ensure the current model is in the set.
                    permitted_models = list(stored)
                    if model not in permitted_models:
                        permitted_models = [model, *permitted_models]
                stored_key = prefs.get("llm_key") or None
            except Exception:
                logger.debug("taos_agent_runtime: could not read taos_agent prefs", exc_info=True)

        # The taOS agent's own LiteLLM virtual key. Reuse the persisted one
        # (re-scoping it to the current permitted set), else mint once and persist
        # it. create_agent_key uses a fixed alias, so re-minting would 400 on the
        # alias collision — persisting the value avoids that and keeps it stable.
        llm_proxy = getattr(app_state, "llm_proxy", None)
        litellm_key: str | None = None
        born_degraded_now = False
        if llm_proxy is None or not llm_proxy.is_running():
            born_degraded_now = True
        if stored_key:
            litellm_key = stored_key
            if llm_proxy is not None:
                try:
                    rescoped = await llm_proxy.update_agent_key(stored_key, permitted_models)
                    if not rescoped:
                        logger.warning(
                            "taos_agent_runtime: re-scoping the taOS agent key returned False "
                            "(key scope may be stale)"
                        )
                except Exception:
                    logger.debug("taos_agent_runtime: re-scoping stored key failed", exc_info=True)
        elif llm_proxy is not None:
            try:
                litellm_key = await llm_proxy.create_agent_key("taos-agent", models=permitted_models)
            except Exception:
                logger.debug("taos_agent_runtime: create_agent_key failed", exc_info=True)
            if litellm_key and desktop_settings is not None:
                try:
                    prefs["llm_key"] = litellm_key
                    await desktop_settings.save_preference("user", "taos_agent", prefs)
                except Exception:
                    logger.debug("taos_agent_runtime: persisting key failed", exc_info=True)
        if not litellm_key:
            litellm_key = get_litellm_master_key(getattr(app_state, "data_dir", None))
        app_state.taos_opencode_key = litellm_key

        # Get the native agent's scoped credential (rotated token) for taOS API access.
        # This replaces the admin local token that was previously used.
        from tinyagentos.native_agent_identity import (
            ensure_native_agent_identity,
            rotate_native_agent_token,
            token_path,
        )
        auth = getattr(app_state, "auth", None)
        signing_key_pem = getattr(app_state, "agent_registry_keypair", (None, None))[0]
        primary_user = auth.get_primary_user() if auth is not None else None
        user_id = primary_user["id"] if primary_user else None
        taos_api_credential: str | None = None
        if user_id and signing_key_pem:
            from tinyagentos.agent_registry_store import AgentRegistryStore
            from tinyagentos.agent_grants_store import AgentGrantsStore
            registry_store = getattr(app_state, "agent_registry", None)
            grants_store = getattr(app_state, "agent_grants", None)
            if registry_store and grants_store:
                await ensure_native_agent_identity(
                    registry=registry_store,
                    grants=grants_store,
                    data_dir=data_dir,
                    signing_key_pem=signing_key_pem,
                    user_id=user_id,
                )
                # Rotate the token so the opencode agent gets a fresh credential.
                new_token = await rotate_native_agent_token(
                    registry=registry_store,
                    data_dir=data_dir,
                    signing_key_pem=signing_key_pem,
                )
                taos_api_credential = new_token
            else:
                # Fallback: read existing token if rotation not possible.
                token_file = token_path(data_dir)
                if token_file.exists():
                    taos_api_credential = token_file.read_text().strip()

        safe_model = _safe_path_component(model)
        home = str(data_dir / f"taos-agent-opencode-{safe_model}") if data_dir else f"taos-agent-opencode-{safe_model}"

        config = getattr(app_state, "config", None)
        taos_port = int((getattr(config, "server", None) or {}).get("port", 6969))
        taos_api_base_url = f"http://127.0.0.1:{taos_port}"

        cfg = OpenCodeServerConfig(
            home=home,
            port=TAOS_OPENCODE_PORT,
            server_password=app_state.taos_opencode_password,
            litellm_base_url=f"http://127.0.0.1:{llm_proxy.port if llm_proxy is not None else 7834}/v1",
            litellm_key=litellm_key,
            model_ids=permitted_models,
            taos_api_base_url=taos_api_base_url,
            taos_api_credential=taos_api_credential,
        )
        server = OpenCodeServer(cfg)
        servers[model] = server
        born_degraded[model] = born_degraded_now
        if model not in sessions:
            sessions[model] = None

    server = servers[model]
    session_id = sessions.get(model)
    # Expose the chosen server's session on the legacy singleton attr so
    # the desktop chat path (taos_agent.py) can read it without change.
    # Must be unconditional: a model with no cached session (None) must
    # clear a stale value left by a previous model.
    app_state.taos_opencode_session_id = session_id
    # Generous deadline: opencode's first run on a fresh home performs a one-time
    # SQLite migration that can take a couple of minutes; a short deadline would
    # spuriously time out the very first taOS-agent chat.
    await server.ensure_running(deadline_s=180.0)
    return server


async def stop_taos_opencode_server(app_state) -> None:
    """Stop all per-agent taOS opencode servers if they were started.

    Safe to call even if no server was ever created. Iterates the per-agent
    cache (taos_opencode_servers) added for concurrent agent support.

    Home directories are NOT removed here — this runs on every ordinary app
    shutdown; deleting homes would discard conversation history and force a
    multi-minute SQLite migration on the next start.  Homes are likewise NOT
    removed on model switch for the same reason: the per-model home is the
    conversation store, and the switch path stops the server but keeps the home.
    """
    servers = getattr(app_state, "taos_opencode_servers", None)
    if not servers:
        return
    for _model, server in list(servers.items()):
        if server is None:
            continue
        try:
            await server.stop()
        except Exception:
            logger.debug("taos_agent_runtime: error during stop", exc_info=True)
    app_state.taos_opencode_servers = {}
    app_state.taos_opencode_sessions = {}
    app_state.taos_opencode_born_degraded = {}
    app_state.taos_opencode_session_id = None

# ---------------------------------------------------------------------------
# PicoClaw lifecycle
# ---------------------------------------------------------------------------


def _picoclaw_lock(app_state) -> asyncio.Lock:
    lock = getattr(app_state, "taos_picoclaw_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app_state.taos_picoclaw_lock = lock
    return lock


async def _picoclaw_models(app_state) -> list[str]:
    """The taOS Agent's own model choice: its model + permitted_models."""
    prefs: dict = {}
    store = getattr(app_state, "desktop_settings", None)
    if store is not None:
        try:
            prefs = await store.get_preference("user", "taos_agent") or {}
        except Exception:
            logger.debug("taos_agent_runtime: could not read taos_agent prefs", exc_info=True)
    models: list[str] = []
    for m in [prefs.get("model"), *(prefs.get("permitted_models") or [])]:
        if isinstance(m, str) and m and m not in models:
            models.append(m)
    return models


def retire_picoclaw(app_state, *, revoke: bool = True) -> int:
    """Revoke the PicoClaw key and delete the secrets its home held: the
    config with the key, and the copy of the taOS credential ``bin/taos``
    reads.

    Idempotent and synchronous (safe at startup). The workspace, where
    PicoClaw keeps its sessions and memory, is kept. Returns how many keys
    were live.
    """
    from tinyagentos.llm_gateway.auth import revoke_keys_for
    from tinyagentos.picoclaw_runtime import scrub_home

    data_dir = getattr(app_state, "data_dir", None)
    revoked = 0
    if data_dir is not None:
        if revoke:
            try:
                revoked = revoke_keys_for(PICOCLAW_PRINCIPAL, data_dir=data_dir)
            except Exception:
                logger.exception("taos_agent_runtime: revoking the picoclaw key failed")
        scrub_home(data_dir)
    app_state.taos_picoclaw_harness = None
    app_state.taos_picoclaw_scope = None
    if revoked:
        logger.info("taos_agent_runtime: picoclaw retired, %d key(s) revoked", revoked)
    return revoked


async def _provision_picoclaw_locked(app_state, models: list[str]):
    from tinyagentos.llm_gateway.auth import mint_gateway_key, revoke_keys_for
    from tinyagentos.picoclaw_runtime import (
        DEFAULT_MODEL,
        PicoClawBinaryNotFoundError,
        PicoClawHarness,
        home_for,
        resolve_picoclaw_binary,
    )
    from tinyagentos.native_agent_identity import (
        ensure_native_agent_identity,
        rotate_native_agent_token,
        token_path,
    )

    binary = resolve_picoclaw_binary()
    if binary is None:
        raise PicoClawBinaryNotFoundError("picoclaw binary not found")
    data_dir = app_state.data_dir
    allow = sorted(set(models) | {DEFAULT_MODEL})
    # One live key at a time: the old one dies before the new one is minted.
    revoke_keys_for(PICOCLAW_PRINCIPAL, data_dir=data_dir)
    key = mint_gateway_key(
        bound_to=PICOCLAW_PRINCIPAL, kind="agent", allowed_models=allow, data_dir=data_dir,
    )
    config = getattr(app_state, "config", None)
    port = int((getattr(config, "server", None) or {}).get("port", 6969))

    # Ensure the native agent identity exists and has the required API scopes.
    # Then rotate its token so the PicoClaw workspace gets a fresh credential.
    auth = getattr(app_state, "auth", None)
    signing_key_pem = getattr(app_state, "agent_registry_keypair", (None, None))[0]
    primary_user = auth.get_primary_user() if auth is not None else None
    user_id = primary_user["id"] if primary_user else None
    if user_id and signing_key_pem:
        # Ensure the native agent identity exists with all required scopes.
        from tinyagentos.agent_registry_store import AgentRegistryStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        registry_store = getattr(app_state, "agent_registry", None)
        grants_store = getattr(app_state, "agent_grants", None)
        if registry_store and grants_store:
            await ensure_native_agent_identity(
                registry=registry_store,
                grants=grants_store,
                data_dir=data_dir,
                signing_key_pem=signing_key_pem,
                user_id=user_id,
            )
            # Rotate the token so the agent gets a fresh credential on each provision.
            new_token = await rotate_native_agent_token(
                registry=registry_store,
                data_dir=data_dir,
                signing_key_pem=signing_key_pem,
            )
            credential = new_token
        else:
            # Fallback: read existing token if rotation not possible.
            credential = None
            token_file = token_path(data_dir)
            if token_file.exists():
                credential = token_file.read_text().strip()
    else:
        credential = None
        token_file = token_path(data_dir)
        if token_file.exists():
            credential = token_file.read_text().strip()

    harness = PicoClawHarness(
        home=home_for(data_dir),
        api_base=f"http://127.0.0.1:{port}/api/llm/v1",
        key=key,
        models=[m for m in models if m != DEFAULT_MODEL],
        binary=binary,
        credential=credential,
        controller_base=f"http://127.0.0.1:{port}",
    )
    harness.write_config()
    app_state.taos_picoclaw_harness = harness
    app_state.taos_picoclaw_scope = tuple(allow)
    return harness


async def provision_picoclaw(app_state):
    """(Re)mint the key and rewrite PicoClaw's config for the current model set."""
    async with _picoclaw_lock(app_state):
        return await _provision_picoclaw_locked(app_state, await _picoclaw_models(app_state))


async def ensure_taos_picoclaw_harness(app_state):
    """The provisioned PicoClaw harness; re-provisioned if the agent's model
    set changed since the key was minted, or the config went missing."""
    from tinyagentos.picoclaw_runtime import DEFAULT_MODEL

    async with _picoclaw_lock(app_state):
        models = await _picoclaw_models(app_state)
        harness = getattr(app_state, "taos_picoclaw_harness", None)
        scope = tuple(sorted(set(models) | {DEFAULT_MODEL}))
        if (
            harness is not None
            and getattr(app_state, "taos_picoclaw_scope", None) == scope
            and harness.config_path.exists()
        ):
            return harness
        return await _provision_picoclaw_locked(app_state, models)


async def apply_framework(app_state) -> FrameworkDecision:
    """Re-decide and make the running harness match: the agent restarts, the
    controller does not. Leaving PicoClaw revokes its key; entering it stops
    opencode and provisions PicoClaw (a fresh key, a fresh config)."""
    decision = refresh_framework_decision(app_state)
    if decision.framework == FRAMEWORK_PICOCLAW:
        await stop_taos_opencode_server(app_state)
        await provision_picoclaw(app_state)
    else:
        async with _picoclaw_lock(app_state):
            retire_picoclaw(app_state)
    return decision


def startup_framework_reconcile(app_state) -> FrameworkDecision:
    """At startup: decide, and if PicoClaw is not the harness, make sure no
    PicoClaw key survived (an operator may have switched by editing
    config.yaml and restarting)."""
    from tinyagentos.litellm_keystore import default_keystore_path

    decision = refresh_framework_decision(app_state)
    data_dir = getattr(app_state, "data_dir", None)
    if decision.framework != FRAMEWORK_PICOCLAW and data_dir is not None:
        # No keystore means no key was ever minted: do not create one just
        # to look (a host that never ran PicoClaw sees no change at all). The
        # credential copy and config are deleted either way.
        retire_picoclaw(app_state, revoke=default_keystore_path(data_dir).exists())
    return decision
