"""MCP server marketplace: curated registry + install flow.

taOS ships a large set of *bundled* MCP plugins (see ``app-catalog/plugins/``),
installed through the app Store.  This module adds the other half of the model —
a *marketplace*: a curated registry of community MCP servers a user can browse
and install in one step, the way a code editor installs an extension.

Three pieces, deliberately separable so each can be tested without a network or
a spawned process:

``MCPRegistryManifest``
    The manifest format.  ``id``/``name``/``description``/``version``/``author``
    plus the install command, the run command, and the *permissions the server
    declares it needs*.  Validated at the load boundary with pydantic, exactly
    like ``tinyagentos.registry.AppManifest``: one malformed entry must never
    abort the listing of the rest.

``MCPRegistry``
    A directory of manifests — the shipped curated snapshot
    (``app-catalog/mcp-registry/``, where every other taOS manifest lives).
    ``MCPRegistry`` takes the directory as a constructor argument, so the same
    loader reads a mounted or downloaded copy of the hosted registry without a
    code change.

``MCPMarketplace``
    The install flow: resolve a manifest -> optionally run its install command
    -> register a *runnable server config* in ``MCPServerStore``.  The config it
    writes uses the ``cmd`` key that ``MCPSupervisor._resolve_cmd`` already
    reads, so an installed marketplace server is launchable by the existing MCP
    loader with no extra wiring.

Declared permissions vs. access grants
    ``permissions`` on a manifest is a *requirement* the server states about
    itself (``filesystem:write`` means "I write files").  It is what the
    installer validates and records in the server config; it is NOT a grant.
    Who may call which tool stays where it already lives — the attachment model
    in ``MCPServerStore``/``check_permission``.  Keeping the two apart means
    installing a server can never silently widen an agent's access.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from tinyagentos.mcp.registry import MCPServerStore

logger = logging.getLogger(__name__)


class InvalidPermissionSet(ValueError):
    """A manifest declared a permission set the runtime does not recognise."""


# The permission vocabulary a marketplace manifest may declare.
#
# Namespaced capabilities take the form ``<area>:<read|write>``; unscoped ones
# are a single word.  Kept small and closed on purpose: an unknown permission is
# rejected rather than ignored, because silently dropping a capability the
# server asked for would install it into an environment it does not expect.
PERMISSION_VOCABULARY: frozenset[str] = frozenset({
    "network",
    "secrets",
    "gpu",
    "container:run",
    "filesystem:read",
    "filesystem:write",
    "database:read",
    "database:write",
})

# Wildcards are refused: a marketplace entry must enumerate what it needs.  The
# whole point of a permission set is that a reviewer can read it, and ``*``
# defeats that while looking shorter.
_WILDCARDS = frozenset({"*", "all", "any", "full"})

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# The only transport the built-in MCP supervisor can run: it launches a
# subprocess and treats stdout as the JSON-RPC channel.  A manifest cannot ask
# for anything else, because the install flow would otherwise register a server
# the loader can never launch (no `cmd` to resolve) and the start route would
# 500 on it.  Rejected at manifest validation, where the error names the entry.
SUPPORTED_TRANSPORTS: frozenset[str] = frozenset({"stdio"})

# Placeholder the installer replaces with a per-server workspace directory it
# creates, so a curated entry can point a server at an empty private directory.
WORKSPACE_TOKEN = "{workspace}"

# The curated registry's own id namespace.
#
# ``mcp_servers`` is ONE namespace shared by every installer on the platform:
# the app Store registers a bundled ``app-catalog/plugins/mcp-*`` manifest under
# its app id, and this marketplace registers a curated entry under its manifest
# id.  An id both surfaces own means the two install paths overwrite each other's
# row — a later Store install replaces the marketplace's launch config with the
# Store's empty one, and a marketplace uninstall deletes a row the Store still
# reports as installed.  Curated entries therefore live under this prefix, so the
# curated namespace is disjoint from the app catalog's by construction;
# ``tests/test_mcp_marketplace.py`` scans the app catalog and fails if it stops
# being true.
MARKETPLACE_ID_PREFIX = "mcp-community-"

# ``config["source"]`` stamps the rows the install flow writes.  It is what
# separates a row this marketplace owns from one another installer wrote (a Store
# plugin row carries no such marker), so the marketplace never overwrites or
# deletes a row that is not its own.
MARKETPLACE_SOURCE = "marketplace"

# Upper bound on a single install command.  Generous enough for a real
# `npm install -g` / `docker pull` on slow hardware, bounded so a hung installer
# cannot hold an admin's request open indefinitely.
INSTALL_TIMEOUT_S = 600.0


def validate_permissions(raw: Any) -> list[str]:
    """Validate and normalise a manifest's declared permission set.

    Rules, all of which must hold for the set to be accepted:

    * it is a sequence of non-empty strings;
    * no wildcard (``*``/``all``/``any``/``full``) — enumerate, don't blanket;
    * every entry is in :data:`PERMISSION_VOCABULARY`;
    * a ``<area>:write`` entry requires the matching ``<area>:read`` — asking
      to write data you have not declared the ability to read is almost always
      an authoring mistake, and it is cheaper to catch here than at runtime.

    Returns the sorted, de-duplicated permission list.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        raise InvalidPermissionSet(
            "permissions must be a list of permission strings, not a string"
        )
    if not isinstance(raw, (list, tuple)):
        raise InvalidPermissionSet(
            f"permissions must be a list, got {type(raw).__name__}"
        )

    normalised: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise InvalidPermissionSet(
                f"permission entries must be non-empty strings, got {entry!r}"
            )
        perm = entry.strip()
        if perm.lower() in _WILDCARDS:
            raise InvalidPermissionSet(
                f"wildcard permission {perm!r} is not allowed; declare the "
                "specific permissions the server needs"
            )
        if perm not in PERMISSION_VOCABULARY:
            raise InvalidPermissionSet(
                f"unknown permission {perm!r}; known permissions: "
                f"{', '.join(sorted(PERMISSION_VOCABULARY))}"
            )
        normalised.add(perm)

    for perm in sorted(normalised):
        area, _, scope = perm.partition(":")
        if scope == "write" and f"{area}:read" not in normalised:
            raise InvalidPermissionSet(
                f"{perm!r} requires {area + ':read'!r} to also be declared"
            )

    return sorted(normalised)


def _coerce_argv(value: Any, *, field: str) -> list[str]:
    """Coerce a manifest command into an argv list.

    A string is split with :func:`shlex.split` (so ``/path/with space/bin`` can
    be quoted), a list is used as-is.  ``None``/``""`` becomes ``[]``.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            return shlex.split(text)
        except ValueError as exc:
            raise ValueError(f"{field} is not a valid command line: {exc}") from exc
    if isinstance(value, (list, tuple)):
        argv: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"{field} entries must be strings, got {item!r}")
            argv.append(item)
        return argv
    raise ValueError(f"{field} must be a string or a list of strings")


class MCPInstallSpec(BaseModel):
    """How to fetch a marketplace server.

    ``command`` is the install argv (``npm install -g …``, ``pip install …``);
    an entry that needs no fetch step (a single-file script served over stdio,
    a remote URL) leaves it empty and the installer skips straight to
    registration.
    """

    model_config = ConfigDict(extra="ignore")

    method: Literal["npm", "pip", "uvx", "docker", "binary", "script", "none"] = "none"
    package: str = ""
    command: list[str] = Field(default_factory=list)

    @field_validator("command", mode="before")
    @classmethod
    def _coerce_command(cls, value: Any) -> list[str]:
        return _coerce_argv(value, field="install.command")


class MCPRunSpec(BaseModel):
    """How to launch the server once installed.

    ``command``/``args`` are the argv the supervisor spawns.  ``env`` holds
    non-secret environment defaults; credentials belong in the secrets store and
    are injected at start time, never in a manifest.

    ``{workspace}`` in ``command``/``args``/``env`` values is substituted by the
    installer with a per-server directory it creates (see
    :data:`WORKSPACE_TOKEN`).  That is how a curated entry points a server at a
    private, empty directory instead of an existing one — the filesystem and git
    servers both need a directory that exists before they start, and pointing
    them at the taOS data directory would hand them secrets and application
    state.
    """

    model_config = ConfigDict(extra="ignore")

    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("command", mode="before")
    @classmethod
    def _coerce_command(cls, value: Any) -> list[str]:
        return _coerce_argv(value, field="run.command")

    @field_validator("args", mode="before")
    @classmethod
    def _coerce_args(cls, value: Any) -> list[str]:
        return _coerce_argv(value, field="run.args")

    def uses_workspace(self) -> bool:
        tokens = [*self.command, *self.args, *self.env.keys(), *self.env.values()]
        return any(WORKSPACE_TOKEN in token for token in tokens)


class MCPRegistryManifest(BaseModel):
    """A validated entry in the MCP marketplace registry.

    ``extra="ignore"`` keeps the format forward-compatible with newer registry
    fields this runtime has not learned about yet, mirroring ``AppManifest``.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str = ""
    version: str = "0.0.0"
    author: str = ""
    homepage: str = ""
    license: str = ""
    categories: list[str] = Field(default_factory=list)
    transport: Literal["stdio", "sse", "http"] = "stdio"
    permissions: list[str] = Field(default_factory=list)
    install: MCPInstallSpec = Field(default_factory=MCPInstallSpec)
    run: MCPRunSpec = Field(default_factory=MCPRunSpec)
    # Registry-curated entries are reviewed by a maintainer; the flag is
    # surfaced in the browse payload so a UI can badge them.
    verified: bool = False
    manifest_path: Path | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        # The character class already requires an alphanumeric first character,
        # so ids made only of separators (``.``, ``..``, ``-``) cannot pass it.
        if not _ID_RE.match(value or ""):
            raise ValueError(
                f"id {value!r} is not a valid marketplace id "
                "(lowercase letters, digits, '.', '_', '-' — must start alphanumeric)"
            )
        return value

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, value: Any) -> str:
        if isinstance(value, bool):
            raise ValueError("version must be a string")
        if isinstance(value, (int, float)):
            return str(value)
        if not isinstance(value, str):
            raise ValueError("version must be a string")
        return value

    @field_validator("permissions", mode="before")
    @classmethod
    def _check_permissions(cls, value: Any) -> list[str]:
        return validate_permissions(value)

    @field_validator("categories", mode="before")
    @classmethod
    def _check_categories(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if not isinstance(value, (list, tuple)):
            raise ValueError("categories must be a list of strings")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("category entries must be non-empty strings")
            out.append(item.strip())
        return out

    @model_validator(mode="after")
    def _check_launchable(self) -> "MCPRegistryManifest":
        if self.transport not in SUPPORTED_TRANSPORTS:
            # Accepting a transport the supervisor cannot run would register a
            # server with no resolvable launch command — installed, listed, and
            # impossible to start.
            raise ValueError(
                f"{self.transport!r} transport is not supported; the MCP "
                f"supervisor launches "
                f"{', '.join(sorted(SUPPORTED_TRANSPORTS))} servers only"
            )
        if not self.run.command:
            raise ValueError(
                "a stdio server needs run.command — there is nothing for "
                "the MCP supervisor to launch"
            )
        return self

    # -- derived views ----------------------------------------------------

    def launch_argv(self, workspace: str | None = None) -> list[str]:
        """The full argv the MCP supervisor will execute.

        ``workspace`` is substituted for every :data:`WORKSPACE_TOKEN`.  A
        manifest that needs a workspace and is asked for its argv without one
        raises rather than shipping an unexpanded ``{workspace}`` to the loader.
        """
        argv = [*self.run.command, *self.run.args]
        if workspace is None:
            if any(WORKSPACE_TOKEN in arg for arg in argv):
                raise ValueError(
                    f"manifest {self.id!r} uses {WORKSPACE_TOKEN} — a workspace "
                    "path is required to build its launch command"
                )
            return argv
        return [arg.replace(WORKSPACE_TOKEN, workspace) for arg in argv]

    def server_config(self, workspace: str | None = None) -> dict:
        """The server config row that makes this manifest loadable.

        ``MCPSupervisor._resolve_cmd`` reads ``config["cmd"]`` first and falls
        back to the app catalog, so a config carrying ``cmd`` is launchable by
        the loader as-is.  ``permissions`` rides along as a record of what the
        server declared, ``source`` records which installer wrote the row (see
        :data:`MARKETPLACE_SOURCE`), and ``env`` supplies non-secret launch
        defaults.
        """
        config: dict[str, Any] = {
            "source": MARKETPLACE_SOURCE,
            "permissions": list(self.permissions),
            "cmd": self.launch_argv(workspace),
        }
        if self.run.env:
            env = dict(self.run.env)
            if workspace is not None:
                env = {
                    key.replace(WORKSPACE_TOKEN, workspace):
                        value.replace(WORKSPACE_TOKEN, workspace)
                    for key, value in env.items()
                }
            config["env"] = env
        return config

    def to_dict(self) -> dict:
        """Browse payload: the manifest fields plus derived launch data.

        ``command`` keeps the raw ``{workspace}`` placeholder — this is a
        *description* of the entry, and expanding it here would need a workspace
        that does not exist until install.  ``uses_workspace`` tells a caller the
        placeholder is there.
        """
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "homepage": self.homepage,
            "license": self.license,
            "categories": list(self.categories),
            "transport": self.transport,
            "permissions": list(self.permissions),
            "verified": self.verified,
            "install_method": self.install.method,
            "install_package": self.install.package,
            "command": [*self.run.command, *self.run.args],
            "uses_workspace": self.run.uses_workspace(),
        }

    # -- construction -----------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict, manifest_path: Path | None = None) -> "MCPRegistryManifest":
        if not isinstance(data, dict):
            raise ValueError("manifest top level is not a mapping")
        return cls.model_validate({**data, "manifest_path": manifest_path})

    @classmethod
    def from_file(cls, path: Path) -> "MCPRegistryManifest":
        """Parse a manifest file, naming the file in any validation error."""
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ValueError(f"manifest {path} has invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"manifest {path} top level is not a mapping")
        try:
            return cls.from_dict(data, manifest_path=path)
        except ValidationError:
            logger.warning("manifest %s failed validation", path, exc_info=True)
            raise


def default_registry_dir() -> Path:
    """The curated registry shipped with this build of taOS.

    It lives next to the rest of the app catalog (``app-catalog/mcp-registry/``,
    one flat ``<id>.yaml`` per entry) rather than inside the package: that is
    where every other taOS manifest already lives, and ``create_app`` locates the
    catalog the same way (``PROJECT_DIR / "app-catalog"``).  ``MCPRegistry``
    takes the directory as an argument, so pointing it at a downloaded or
    mounted copy of the hosted registry is a one-line change.
    """
    return Path(__file__).resolve().parent.parent.parent / "app-catalog" / "mcp-registry"


def _manifest_files(registry_dir: Path) -> Iterable[Path]:
    """Yield manifest files under *registry_dir* in a stable order.

    Both layouts the app catalog already uses are accepted: a flat
    ``<id>.yaml`` and a per-entry ``<id>/manifest.yaml`` directory.  An
    unreadable directory yields nothing rather than raising — the registry is
    browsable-but-empty instead of a 500.
    """
    try:
        if not registry_dir.is_dir():
            return []
        entries = sorted(registry_dir.iterdir())
    except OSError:
        logger.warning("mcp marketplace: cannot read registry dir %s", registry_dir, exc_info=True)
        return []
    files: list[Path] = []
    for path in entries:
        try:
            if path.is_dir():
                candidate = path / "manifest.yaml"
                if candidate.is_file():
                    files.append(candidate)
            elif path.suffix in (".yaml", ".yml"):
                files.append(path)
        except OSError:
            logger.warning("mcp marketplace: cannot stat %s", path, exc_info=True)
            continue
    return files


class MCPRegistry:
    """A directory of curated MCP marketplace manifests.

    Loading is deferred and guarded by a lock, mirroring ``AppRegistry``: a
    boot does not pay for parsing every manifest, and a concurrent first read
    from two request handlers parses once.  A malformed entry is logged and
    skipped — the rest of the registry still lists — but it is also recorded in
    :attr:`errors` so an operator (or the reload route) can see it instead of
    wondering why an entry is missing.
    """

    def __init__(self, registry_dir: Path):
        self.registry_dir = Path(registry_dir)
        self._manifests: dict[str, MCPRegistryManifest] = {}
        self._errors: list[dict[str, str]] = []
        self._loaded = False
        # Reentrant: _ensure_loaded() holds the lock while calling load(), which
        # publishes under the same lock.  A plain Lock would deadlock there.
        self._lock = threading.RLock()

    # -- loading ----------------------------------------------------------

    def _read_dir(self) -> tuple[dict[str, MCPRegistryManifest], list[dict[str, str]]]:
        """Parse the registry directory.  Pure — no shared state touched.

        A per-entry failure of *any* kind (unparseable YAML, schema violation,
        an unreadable file) is collected into ``errors`` rather than raised:
        one bad entry must never take the whole listing down with it.
        """
        manifests: dict[str, MCPRegistryManifest] = {}
        errors: list[dict[str, str]] = []
        if not self.registry_dir.is_dir():
            # A registry directory that is not there is the one load failure an
            # operator cannot see from the listing: the marketplace still
            # renders, just empty.  Record it (the reload route reports
            # ``errors``) instead of leaving an empty catalogue unexplained.
            # `is_dir()` is False only for a genuinely missing path or a
            # non-directory; an unreadable directory still stats as a directory
            # and is reported by `_manifest_files` in the log.
            errors.append({
                "path": str(self.registry_dir),
                "error": "registry directory not found",
            })
            logger.warning(
                "mcp marketplace: registry dir %s does not exist", self.registry_dir
            )
            return manifests, errors
        for path in _manifest_files(self.registry_dir):
            try:
                manifest = MCPRegistryManifest.from_file(path)
            except (ValidationError, ValueError, OSError) as exc:
                errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
                continue
            if manifest.id in manifests:
                errors.append({
                    "path": str(path),
                    "error": f"duplicate id {manifest.id!r} already defined by "
                             f"{manifests[manifest.id].manifest_path}",
                })
                continue
            manifests[manifest.id] = manifest
        return manifests, errors

    def _publish(
        self,
        manifests: dict[str, MCPRegistryManifest],
        errors: list[dict[str, str]],
    ) -> None:
        # Assign under the lock so a reader never sees a half-replaced registry.
        with self._lock:
            self._manifests = manifests
            self._errors = errors
            self._loaded = True

    def load(self) -> None:
        """(Re)read the registry directory.

        A direct ``load()`` (or ``reload()``) parses outside the lock and then
        publishes under it, so a reader holding an older snapshot is never
        blocked behind a slow filesystem.  Called from :meth:`_ensure_loaded`
        the parse runs *inside* the lock instead — that is deliberate: it is
        what makes the lazy first read happen exactly once.  Either way the
        parse is off the lock for readers who already have a snapshot.
        """
        self._publish(*self._read_dir())

    def _ensure_loaded(self) -> None:
        # Double-checked locking: the second check is taken *while holding* the
        # lock (so it serialises concurrent first readers), and the parse then
        # runs under that same reentrant lock. A concurrent reload() therefore
        # cannot have its newer snapshot clobbered by an in-flight lazy load.
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self.load()

    def reload(self) -> None:
        self.load()

    # -- reads ------------------------------------------------------------

    @property
    def errors(self) -> list[dict[str, str]]:
        self._ensure_loaded()
        return list(self._errors)

    def get(self, manifest_id: str) -> MCPRegistryManifest | None:
        self._ensure_loaded()
        return self._manifests.get(manifest_id)

    def list(
        self,
        query: str | None = None,
        category: str | None = None,
    ) -> list[MCPRegistryManifest]:
        """List entries, optionally filtered by free text and/or category.

        The free-text match is a case-insensitive substring over id, name,
        description, author and categories — enough for a search box without
        pulling in a fuzzy matcher.
        """
        self._ensure_loaded()
        results = sorted(self._manifests.values(), key=lambda m: m.name.lower())
        if category:
            wanted = category.strip().lower()
            results = [
                m for m in results
                if any(c.lower() == wanted for c in m.categories)
            ]
        if query:
            needle = query.strip().lower()
            if needle:
                results = [
                    m for m in results
                    if needle in " ".join([
                        m.id, m.name, m.description, m.author,
                        *m.categories,
                    ]).lower()
                ]
        return results

    def categories(self) -> list[str]:
        self._ensure_loaded()
        seen: set[str] = set()
        for manifest in self._manifests.values():
            seen.update(manifest.categories)
        return sorted(seen, key=str.lower)


# An install-command runner: argv -> (returncode, stdout, stderr).
InstallRunner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


class MCPMarketplaceError(Exception):
    """An install/browse action failed.  ``status_code`` is the HTTP hint."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL a child and reap it, tolerating an already-dead process."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.wait()
    except Exception:  # pragma: no cover - reaping best effort
        logger.warning("mcp marketplace: could not reap install process", exc_info=True)


async def _subprocess_runner(
    argv: list[str],
    *,
    timeout: float = INSTALL_TIMEOUT_S,
) -> tuple[int, str, str]:
    """Default runner: execute the install command and capture its output.

    Bounded on purpose.  The route awaits this directly and the ASGI server has
    a graceful-shutdown timeout, not a request timeout, so an install command
    that never exits would hold the request (and the admin's browser) open
    forever.  On timeout the child is killed and reaped and a non-zero code is
    returned, which ``install`` turns into the same 502-and-leave-the-store-alone
    path as any other failed install.  Cancellation kills the child too — a
    disconnected client must not leak a running installer.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate(proc)
        return (
            124,
            "",
            f"install command timed out after {timeout:g}s and was killed",
        )
    except asyncio.CancelledError:
        await _terminate(proc)
        raise
    return (
        proc.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


def _is_marketplace_row(server: dict | None) -> bool:
    """True when *server* is a row this marketplace wrote.

    The origin is recorded in ``config["source"]``.  A row registered by the app
    Store (or by anything else) carries no such marker and is therefore never
    treated as this marketplace's own — which is what keeps an install from
    overwriting it and an uninstall from deleting it.
    """
    if not server:
        return False
    config = server.get("config")
    return isinstance(config, dict) and config.get("source") == MARKETPLACE_SOURCE


class MCPMarketplace:
    """Browse the registry and install entries into ``MCPServerStore``.

    The install command is executed through an injectable ``runner`` so the
    flow can be tested end-to-end (manifest -> registered, launchable config)
    without fetching anything from the network.  ``run_install_command=False``
    resolves and registers without executing, which is what a test or a
    dry-run preview uses.

    ``workspace_root`` is where a manifest's :data:`WORKSPACE_TOKEN` is expanded
    to: ``<workspace_root>/<id>``, created by the install flow.  A manifest that
    needs a workspace cannot be installed without one — the installer refuses
    rather than writing a config with a literal ``{workspace}`` in it.
    """

    def __init__(
        self,
        registry: MCPRegistry,
        store: MCPServerStore,
        supervisor: Any | None = None,
        runner: InstallRunner | None = None,
        workspace_root: Path | None = None,
    ):
        self.registry = registry
        self.store = store
        self.supervisor = supervisor
        self._runner: InstallRunner = runner or _subprocess_runner
        self.workspace_root = Path(workspace_root) if workspace_root else None

    # -- browse -----------------------------------------------------------

    def categories(self) -> list[str]:
        return self.registry.categories()

    async def _installed_rows(self) -> dict[str, dict]:
        """Every server row in the store, keyed by id — whoever installed it."""
        return {s["id"]: s for s in await self.store.list_servers()}

    async def browse(
        self,
        query: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        """Registry entries annotated with their install/running state.

        ``installed`` is true whenever the id is occupied — the namespace is
        shared with the app Store, so an entry can be "present" because a
        bundled plugin owns the id.  ``installed_by_marketplace`` says whether
        the row is one this marketplace wrote, i.e. whether the entry's own
        launch config is the one stored.
        """
        rows = await self._installed_rows()
        entries: list[dict] = []
        for manifest in self.registry.list(query=query, category=category):
            row = rows.get(manifest.id)
            entry = manifest.to_dict()
            entry["installed"] = row is not None
            entry["installed_by_marketplace"] = _is_marketplace_row(row)
            entry["running"] = False
            if entry["installed"] and self.supervisor is not None:
                status = self.supervisor.get_status(manifest.id)
                entry["running"] = bool(status.get("running"))
            entries.append(entry)
        return entries

    def get_manifest(self, manifest_id: str) -> MCPRegistryManifest:
        manifest = self.registry.get(manifest_id)
        if manifest is None:
            raise MCPMarketplaceError(
                f"marketplace entry {manifest_id!r} not found", status_code=404
            )
        return manifest

    async def detail(self, manifest_id: str) -> dict:
        manifest = self.get_manifest(manifest_id)
        entry = manifest.to_dict()
        entry["installed"] = False
        entry["installed_by_marketplace"] = False
        entry["running"] = False
        server = await self.store.get_server(manifest_id)
        if server is not None:
            entry["installed"] = True
            entry["installed_by_marketplace"] = _is_marketplace_row(server)
            entry["installed_version"] = server.get("version", "")
            entry["installed_config"] = server.get("config", {})
            if self.supervisor is not None:
                entry["running"] = bool(
                    self.supervisor.get_status(manifest_id).get("running")
                )
        return entry

    # -- install ----------------------------------------------------------

    async def install(
        self,
        manifest_id: str,
        *,
        run_install_command: bool = True,
    ) -> dict:
        """Resolve a manifest and register a loadable server config for it.

        Raises :class:`MCPMarketplaceError` when the entry is unknown (404),
        already installed (409), or its install command fails (502).  A row the
        marketplace did not write (the id belongs to a bundled Store app) is
        also refused with 409, explicitly and without touching it — see
        :data:`MARKETPLACE_ID_PREFIX` for why the curated ids avoid that
        altogether.  A failed install leaves the store untouched — the server is
        registered only after the fetch step succeeded, so a half-installed
        entry never shows up as launchable.

        The already-installed check is not atomic with the registration that
        follows it: two concurrent installs of the same id would both run the
        install command.  That is bounded and accepted here — the route is
        admin-only, both writers write the *same* config for the same manifest
        (``register_server`` is an ``INSERT OR REPLACE`` on identical values),
        and closing the window properly needs a per-id lock bound to the running
        event loop (the platform has more than one).  The store row remains the
        single record of truth.
        """
        manifest = self.get_manifest(manifest_id)

        existing = await self.store.get_server(manifest.id)
        if existing is not None:
            if _is_marketplace_row(existing):
                raise MCPMarketplaceError(
                    f"{manifest.id!r} is already installed", status_code=409
                )
            # ``mcp_servers`` is one namespace shared with the app Store, which
            # registers a bundled plugin under its app id with no config.  A row
            # this marketplace did not write is reported as what it is: silently
            # replacing it would destroy the other installer's row (turning a
            # working server into one whose launch command no longer resolves),
            # and the bare "already installed" would read as *this* entry being
            # present when its config is not the one stored.
            raise MCPMarketplaceError(
                f"server id {manifest.id!r} is already in use by a server "
                "installed outside the marketplace (for example a bundled app "
                "from the Store); the marketplace will not overwrite it — "
                "uninstall it first, or install a registry entry with a "
                "different id",
                status_code=409,
            )

        if any(WORKSPACE_TOKEN in arg for arg in manifest.install.command):
            # The placeholder expands from the run configuration only; an install
            # command carrying it would be executed with the literal token.
            raise MCPMarketplaceError(
                f"{manifest.id!r} uses {WORKSPACE_TOKEN} in install.command, "
                "which is not supported — the placeholder belongs in the run "
                "configuration",
                status_code=400,
            )

        # Resolve (and validate) the workspace before anything is fetched, so a
        # missing workspace root fails without running the install command.
        workspace = self._workspace_path(manifest)
        install_output: str | None = None

        if run_install_command and manifest.install.command:
            returncode, stdout, stderr = await self._runner(manifest.install.command)
            if returncode != 0:
                raise MCPMarketplaceError(
                    f"install command for {manifest.id!r} failed "
                    f"(exit {returncode}): {stderr.strip()[:400] or stdout.strip()[:400]}",
                    status_code=502,
                )
            install_output = stdout[-2000:]

        # …and create it only once the install succeeded, so a failed or timed
        # out install leaves no directory behind.
        self._create_workspace(workspace)
        config = manifest.server_config(workspace)

        await self.store.register_server(
            manifest.id, manifest.version, manifest.transport, config
        )
        logger.info(
            "mcp marketplace: installed %s v%s (%s, %d declared permissions)",
            manifest.id, manifest.version, manifest.transport,
            len(manifest.permissions),
        )
        return {
            "status": "installed",
            "server_id": manifest.id,
            "version": manifest.version,
            "transport": manifest.transport,
            "config": config,
            "permissions": list(manifest.permissions),
            "workspace": workspace,
            "install_output": install_output,
        }

    def _workspace_path(self, manifest: MCPRegistryManifest) -> str | None:
        """The workspace a manifest needs, or ``None`` if it needs none.

        Only a manifest that actually references :data:`WORKSPACE_TOKEN` gets a
        directory — an entry that needs no filesystem scope should not scatter
        empty directories through the data dir.  A manifest that needs one and
        cannot get it is refused here, before anything is fetched or registered:
        a config with a literal ``{workspace}`` in its argv is not launchable,
        and installing it would leave a server that can never start.
        """
        if not manifest.run.uses_workspace():
            return None
        if self.workspace_root is None:
            raise MCPMarketplaceError(
                f"{manifest.id!r} needs a workspace but the marketplace has no "
                "workspace root configured",
                status_code=500,
            )
        return str(self.workspace_root / manifest.id)

    def _create_workspace(self, workspace: str | None) -> None:
        """Create the workspace directory, preserving anything already there."""
        if workspace is None:
            return
        try:
            Path(workspace).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MCPMarketplaceError(
                f"could not create the workspace at {workspace}: {exc}",
                status_code=500,
            ) from exc

    async def uninstall(self, manifest_id: str) -> dict:
        """Remove an installed marketplace server.

        Delegates to the supervisor when one is wired (it also drops the
        server's attachments and any ``mcp:<id>:`` secrets — the "no leftover
        state" half of a clean uninstall), and falls back to the store.

        Only a row this marketplace wrote is removed: the id namespace is shared
        with the app Store, and deleting a Store-owned row here would leave the
        Store still reporting the app as installed while its server row is gone.
        """
        server = await self.store.get_server(manifest_id)
        if server is None:
            raise MCPMarketplaceError(
                f"{manifest_id!r} is not installed", status_code=404
            )
        if not _is_marketplace_row(server):
            raise MCPMarketplaceError(
                f"{manifest_id!r} was not installed from the marketplace (its "
                "server row was written by another installer, for example the "
                "app Store); the marketplace will not remove it",
                status_code=409,
            )
        if self.supervisor is not None:
            result = await self.supervisor.uninstall(manifest_id)
        else:
            await self.store.delete_server(manifest_id)
            result = {"agents_affected": [], "env_secrets_dropped": 0}
        result["status"] = "uninstalled"
        result["server_id"] = manifest_id
        return result
