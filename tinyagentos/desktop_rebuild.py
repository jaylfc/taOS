"""Rebuild the desktop frontend bundle if source has moved ahead of the bundle.

Used by both the in-app Install Update handler and the background auto-update
service.  Mirrors the intent of ExecStartPre in the systemd unit and
bin/update.sh — so all update paths converge on the same conditional rebuild
regardless of platform (systemd/Pi, Docker, Mac .app, dev host).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Marker recording the package-lock.json hash of the last successful
# `npm install`. Lives inside node_modules so it is naturally per-install
# (wiped whenever node_modules is) and never tracked by git.
_DEPS_MARKER = "node_modules/.taos-deps-lock"


@dataclass(frozen=True)
class RebuildResult:
    """Outcome of a rebuild attempt.

    ``rebuilt`` indicates whether a rebuild was attempted at all (False when
    the staleness check skipped it or npm wasn't available).  ``success``
    indicates whether the result is healthy (False on npm failure or
    timeout).  ``message`` is human-readable for log/error surfaces.

    Callers can branch on ``success`` directly rather than string-matching
    the message — see issue #327.
    """
    rebuilt: bool
    success: bool
    message: str


def _is_desktop_build_input(rel_path: str) -> bool:
    """Return True if a repo-relative path is an input to the desktop build.

    The set mirrors what scripts/rebuild-desktop.sh compares against the
    bundle: everything under desktop/src plus the dependency and build-tool
    config (package.json, the lock-files, vite.config.*, tsconfig*.json).
    Installed dependencies are not inputs -- node_modules/ is a build *output*
    of the lock-file, and npm rewrites thousands of package.json files in there
    on every install.
    """
    prefix = "desktop/"
    if not rel_path.startswith(prefix):
        return False
    tail = rel_path[len(prefix):]
    if tail.startswith("node_modules/") or "/node_modules/" in tail:
        return False
    if tail == "" or tail.startswith("src/"):
        return True  # the whole tree, or anything under src/
    name = tail.rsplit("/", 1)[-1]
    return (
        name == "package.json"
        or name.startswith("vite.config.")
        or (name.startswith("tsconfig") and name.endswith(".json"))
        or "-lock." in name
    )


def _newest_build_input_mtime(desktop_dir: Path) -> float:
    """Newest mtime across the desktop build inputs (0.0 when there are none)."""
    newest = 0.0
    src_dir = desktop_dir / "src"
    if src_dir.is_dir():
        for path in src_dir.rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
    # Dependency + build-tool config lives at the top of desktop/; node_modules
    # is deliberately not walked (see _is_desktop_build_input).
    for path in desktop_dir.iterdir():
        if path.is_file() and _is_desktop_build_input(f"desktop/{path.name}"):
            newest = max(newest, path.stat().st_mtime)
    return newest


def _is_bundle_stale(project_root: Path) -> bool:
    """Return True if any desktop build input is newer than the built bundle.

    "Build input" means desktop/src/** plus the dependency and build-tool
    config, matching scripts/rebuild-desktop.sh -- a bumped package.json,
    lock-file, vite.config.* or tsconfig*.json changes the output just as a
    source edit does.
    """
    desktop_dir = project_root / "desktop"
    if not desktop_dir.is_dir():
        return False  # nothing to build
    index_html = project_root / "static" / "desktop" / "index.html"
    if not index_html.is_file():
        return True  # never built
    return _newest_build_input_mtime(desktop_dir) > index_html.stat().st_mtime


def _lockfile_hash(desktop_dir: Path) -> str | None:
    """SHA-256 of desktop/package-lock.json, or None if it doesn't exist."""
    lock = desktop_dir / "package-lock.json"
    if not lock.is_file():
        return None
    return hashlib.sha256(lock.read_bytes()).hexdigest()


def _deps_install_needed(desktop_dir: Path) -> bool:
    """True if ``npm install`` must run before a build.

    Skips the install only when node_modules already exists and the
    package-lock.json hash matches the last successful install. Any of
    {no node_modules, no lockfile to compare, lockfile changed, marker
    unreadable} forces the install — so the gate never trades correctness
    for speed.
    """
    if not (desktop_dir / "node_modules").is_dir():
        return True
    current = _lockfile_hash(desktop_dir)
    if current is None:
        return True  # no lockfile to trust — always install
    try:
        return (desktop_dir / _DEPS_MARKER).read_text().strip() != current
    except OSError:
        return True


def _record_deps_install(desktop_dir: Path) -> None:
    """Record the current package-lock hash after a successful npm install."""
    current = _lockfile_hash(desktop_dir)
    if current is None:
        return
    try:
        (desktop_dir / _DEPS_MARKER).write_text(current)
    except OSError as exc:  # node_modules vanished mid-build, read-only fs, etc.
        logger.warning("Could not write deps marker (%s) — next update reinstalls.", exc)


async def _get_desktop_tree_sha(project_root: Path) -> str | None:
    """Return the git tree SHA of desktop/, or None if git is unavailable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(project_root),
            "rev-parse",
            "HEAD:desktop",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode == 0:
            return out.decode(errors="replace").strip()
    except FileNotFoundError:
        pass
    return None


def _porcelain_paths(out: bytes) -> tuple[str, ...]:
    """Repo-relative paths from ``git status --porcelain`` output.

    Handles the rename form (``R  old -> new``) by keeping the destination and
    strips the quoting git applies to paths with unusual characters.
    """
    paths: list[str] = []
    for line in out.decode(errors="replace").splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return tuple(paths)


async def _desktop_tree_status(project_root: Path) -> tuple[bool, tuple[str, ...]]:
    """Return ``(verified, dirty_paths)`` for the desktop/ working tree.

    ``verified`` is False when git could not answer (not installed, no/broken
    .git, unreadable index); callers must not read anything into the empty
    path list in that case.
    """
    desktop_dir = project_root / "desktop"
    if not desktop_dir.is_dir():
        return False, ()
    try:
        # --untracked-files=normal (the default) is deliberate: an untracked
        # file always shows up -- as itself or as its untracked parent
        # directory -- so "all" would descend into every untracked directory
        # under desktop/ for no extra signal, which is real I/O on SD-card
        # hosts (Pi 4).
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(project_root),
            "status",
            "--porcelain",
            "--untracked-files=normal",
            "--",
            "desktop",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return False, ()
        return True, _porcelain_paths(out)
    except FileNotFoundError:
        return False, ()


async def _is_desktop_working_tree_clean(project_root: Path) -> bool:
    """Return True if the desktop/ tree has no tracked edits or untracked build inputs.

    Provenance is only a reliable proof of freshness when the working tree
    matches the committed HEAD:desktop. A dirty tree (tracked modifications or
    new build inputs the user has not committed) means the recorded marker
    could predate local edits and would skip a needed rebuild.  An unverifiable
    tree (no git) is treated as dirty for the same reason.
    """
    verified, dirty = await _desktop_tree_status(project_root)
    return verified and not dirty


async def _dirty_desktop_build_inputs(project_root: Path) -> tuple[str, ...]:
    """Return the modified/untracked desktop *build inputs*, if git can tell us.

    Rejecting the provenance marker is not enough on its own: the mtime
    fallback then decides, and a dirty build input can easily be *older* than
    the bundle (restored from a backup or an archive that preserved mtimes, or
    written on a clock-skewed host -- the very unreliability this marker
    exists to work around).  A dirty build input therefore forces the rebuild.

    An unverifiable tree returns () on purpose: git-less deployments (installed
    from a tarball) would otherwise run a full npm rebuild on every single
    service start, which is the failure this whole check exists to prevent.
    Their marker is already distrusted, so they keep the mtime heuristic that
    predates provenance.
    """
    verified, dirty = await _desktop_tree_status(project_root)
    if not verified:
        return ()
    return tuple(path for path in dirty if _is_desktop_build_input(path))


async def _is_bundle_provenance_current(project_root: Path) -> bool:
    """Return True if the bundle provenance marker matches the current desktop tree.

    A marker written by a successful prebuilt-bundle install or local build
    records the ``git rev-parse HEAD:desktop`` SHA at build time.  If the
    current tree SHA matches, the bundle is known-good regardless of
    filesystem mtimes (which can be misleading after a fetch or on hosts
    with clock skew).

    Also requires a clean desktop working tree and a present bundle
    (``static/desktop/index.html``): provenance can only prove freshness
    against the committed tree, so a matching marker with local edits or
    untracked build inputs must fall through to the mtime check, and a
    matching marker with no bundle must not report success.
    """
    index_html = project_root / "static" / "desktop" / "index.html"
    if not index_html.is_file():
        return False  # marker surviving but bundle missing -> rebuild
    marker = project_root / "static" / "desktop" / ".taos-bundle-provenance"
    if not marker.is_file():
        return False
    try:
        recorded = marker.read_text().strip()
    except OSError:
        return False
    current = await _get_desktop_tree_sha(project_root)
    if not (current and current == recorded):
        return False
    return await _is_desktop_working_tree_clean(project_root)


def _record_bundle_provenance(project_root: Path, tree_sha: str) -> None:
    """Record the desktop tree SHA that the current bundle was built from.

    The marker records a *committed* tree SHA (``HEAD:desktop``), so callers
    must only write it when the bundle really was built from that tree: the
    prebuilt-bundle install (which is keyed by that SHA) or a local build from
    a clean desktop working tree.  ``_is_bundle_provenance_current`` relies on
    that invariant and re-checks the working tree before trusting the marker.

    The SHA is normalized (no trailing whitespace) so the read path's
    ``.strip()`` is symmetric and tolerant of future git wrappers that
    might not emit a trailing newline.

    Marker failures are never fatal: a missing marker only costs the next
    invocation a fall-through to the mtime check, so a freshly built bundle
    must not be reported as a failed rebuild because the marker could not be
    written -- the mkdir is inside the try for exactly that reason.
    """
    marker = project_root / "static" / "desktop" / ".taos-bundle-provenance"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(tree_sha.strip() + "\n")
    except OSError as exc:
        logger.warning(
            "Could not write bundle provenance marker (%s); next update may "
            "fall through to the mtime path.", exc,
        )


def _clear_bundle_provenance(project_root: Path) -> None:
    """Drop the provenance marker when the bundle cannot be attributed to HEAD.

    A bundle built from a dirty desktop/ tree does not correspond to any
    committed tree SHA.  Leaving an older marker in place would let a later
    revert back to that SHA present a clean tree plus a matching marker in
    front of a bundle that was built from the edited source.
    """
    marker = project_root / "static" / "desktop" / ".taos-bundle-provenance"
    try:
        marker.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning(
            "Could not remove stale bundle provenance marker (%s); it may "
            "skip a needed rebuild once the working tree is clean again.", exc,
        )


_BUNDLE_BASE = "https://github.com/jaylfc/taOS/releases/download/bundle-latest"


async def _try_prebuilt_desktop_bundle(project_root: Path) -> bool:
    """Install the CI-published prebuilt SPA bundle when it matches this source.

    The bundle is keyed by ``git rev-parse HEAD:desktop`` (the content hash of
    the frontend tree), so it is valid for every commit that does not touch
    desktop/. On a match we download + stage + swap it into static/desktop/ and
    return True, letting the caller skip the memory-heavy vite build that OOMs
    on small machines. Returns False -- fall back to a local build -- on any
    mismatch, missing git, or network/extract failure. The artifact is our own
    CI output, extracted with the path-safe ``data`` tar filter.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(project_root), "rev-parse", "HEAD:desktop",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except FileNotFoundError:
        return False  # git not on PATH
    local_tree = out.decode(errors="replace").strip()
    if proc.returncode != 0 or not local_tree:
        return False

    def _get(url: str, *, binary: bool):
        import urllib.request
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        return data if binary else data.decode(errors="replace").strip()

    # Only download the (larger) bundle if the published tree matches ours.
    try:
        remote_tree = await asyncio.to_thread(_get, f"{_BUNDLE_BASE}/desktop-tree.txt", binary=False)
    except Exception:
        return False
    if remote_tree != local_tree:
        return False

    logger.info("Downloading prebuilt desktop bundle (matches source; skipping local build).")
    try:
        blob = await asyncio.to_thread(_get, f"{_BUNDLE_BASE}/desktop-bundle.tar.gz", binary=True)
    except Exception as exc:
        logger.warning("Prebuilt bundle download failed (%s); building locally.", exc)
        return False

    # Verify against the CI-published SHA256 before extracting; reject a corrupted
    # or tampered tarball and fall back to a local build.
    try:
        expected_sha = await asyncio.to_thread(_get, f"{_BUNDLE_BASE}/desktop-bundle.sha256", binary=False)
    except Exception:
        expected_sha = ""
    if not expected_sha or hashlib.sha256(blob).hexdigest() != expected_sha.split()[0]:
        logger.warning("Prebuilt bundle checksum missing or mismatched; building locally.")
        return False

    import io
    import shutil
    import tarfile
    import tempfile

    # Stage INSIDE static/ (same filesystem as the target) so the final swap is
    # an atomic rename, never a cross-device copy that could fail half-done and
    # leave static/desktop missing.
    static_dir = project_root / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".taos-bundle-", dir=str(static_dir)))
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            try:
                tar.extractall(stage, filter="data")  # py>=3.12 path-safe filter
            except TypeError:
                # Pythons without the path-safe tar filter: do not risk an unsafe
                # extract; fall back to the local build instead.
                logger.warning("This Python lacks the path-safe tar filter; building locally.")
                return False
        staged = stage / "desktop"
        if not (staged / "index.html").is_file():
            logger.warning("Prebuilt bundle missing index.html; building locally.")
            return False
        target = static_dir / "desktop"
        if target.exists():
            shutil.rmtree(target)
        staged.rename(target)  # atomic rename (same filesystem)
        # The tarball preserves the CI build mtime, which can predate the local
        # source and make _is_bundle_stale treat the bundle as perpetually stale
        # (re-downloading on every check). Stamp index.html fresh and record
        # provenance so the rebuild trigger can compare content hashes instead.
        (target / "index.html").touch()
        _record_bundle_provenance(project_root, local_tree)
        logger.info("Prebuilt desktop bundle installed into static/desktop/.")
        return True
    except Exception as exc:
        logger.warning("Prebuilt bundle install failed (%s); building locally.", exc)
        return False
    finally:
        shutil.rmtree(stage, ignore_errors=True)


async def rebuild_desktop_bundle_if_stale(
    project_root: Path,
    *,
    timeout_seconds: int = 600,
    force: bool = False,
) -> RebuildResult:
    """Run npm install + npm run build if the bundle is stale (or always, if force=True).

    Returns a :class:`RebuildResult` with ``rebuilt`` (was a build attempted?),
    ``success`` (did it succeed?), and ``message`` (human-readable detail).

    On hosts where npm/node aren't installed the rebuild reports
    ``rebuilt=False, success=True`` (the skip is a successful no-op for the
    caller — it's not the rebuild's job to install npm).

    Use ``force=True`` for explicit user-initiated rebuilds (e.g. the
    ``/api/settings/rebuild-frontend`` endpoint or applied updates) where the
    staleness heuristic isn't trustworthy — committed bundles can lie about
    their freshness when a PR landed source-only.
    """
    if not force:
        # Provenance check first: a fetched (or locally built) bundle whose
        # recorded tree SHA matches the current desktop/ source is always
        # current, regardless of filesystem mtimes.
        if await _is_bundle_provenance_current(project_root):
            return RebuildResult(
                rebuilt=False,
                success=True,
                message="Desktop bundle provenance is current — skipping rebuild.",
            )
        if not _is_bundle_stale(project_root):
            # mtime says "fresh", but mtimes cannot see a build input whose
            # content differs from what was built while its timestamp does not
            # (backup/archive restore, clock skew). If git can name dirty build
            # inputs, they win over the heuristic.
            dirty_inputs = await _dirty_desktop_build_inputs(project_root)
            if not dirty_inputs:
                return RebuildResult(
                    rebuilt=False,
                    success=True,
                    message="Desktop bundle is current — skipping rebuild.",
                )
            logger.info(
                "Desktop build inputs are modified but not newer than the bundle "
                "(%s) — rebuilding.", ", ".join(dirty_inputs[:5]),
            )

    desktop_dir = project_root / "desktop"
    if not (desktop_dir / "package.json").is_file():
        return RebuildResult(
            rebuilt=False,
            success=True,
            message="No desktop/package.json found — skipping rebuild.",
        )

    logger.info("Desktop source is ahead of bundle — rebuilding...")

    # Prefer the CI-published prebuilt bundle (keyed to this desktop/ source by
    # its git tree SHA) so low-RAM hosts skip the memory-heavy vite build. Falls
    # through to the local build below on any miss.
    if await _try_prebuilt_desktop_bundle(project_root):
        return RebuildResult(
            rebuilt=True,
            success=True,
            message="Installed prebuilt desktop bundle (no local build needed).",
        )

    try:
        if _deps_install_needed(desktop_dir):
            # Use `npm ci`, not `npm install`: ci installs exactly from the
            # committed package-lock.json and NEVER rewrites it. `npm install`
            # rewrites the lockfile, which leaves the tracked desktop/package-
            # lock.json dirty and makes the next in-app `git pull` update abort
            # with "local changes would be overwritten by merge" (the deadlock a
            # user hit when their installed version predated the #852 restore).
            logger.info("Dependencies changed or missing — running npm ci...")
            proc = await asyncio.create_subprocess_exec(
                "npm", "ci", "--silent",
                cwd=str(desktop_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
            if proc.returncode != 0:
                # `npm ci` fails if package.json and the lockfile are out of sync
                # (rare). Fall back to `npm install`, then restore the lockfile so
                # the tree stays clean for the next update.
                logger.warning(
                    "npm ci failed (rc=%s), falling back to npm install: %s",
                    proc.returncode, stderr.decode(errors="replace")[-300:],
                )
                proc = await asyncio.create_subprocess_exec(
                    "npm", "install", "--silent",
                    cwd=str(desktop_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
                if proc.returncode != 0:
                    msg = f"npm install failed (rc={proc.returncode}): {stderr.decode(errors='replace')[-500:]}"
                    logger.error(msg)
                    return RebuildResult(rebuilt=True, success=False, message=msg)
                # Discard the lockfile rewrite npm install just made so the tree
                # is clean for the next git-pull update. If this restore fails we
                # only warn: apply_update() also restores the lockfile before
                # every pull, so a dirty lockfile is double-covered there.
                try:
                    restore = await asyncio.create_subprocess_exec(
                        "git", "checkout", "--", "package-lock.json",
                        cwd=str(desktop_dir),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, rerr = await restore.communicate()
                    if restore.returncode != 0:
                        logger.warning(
                            "could not restore package-lock.json after npm install "
                            "(rc=%s): %s",
                            restore.returncode, rerr.decode(errors="replace")[-200:],
                        )
                except FileNotFoundError:
                    logger.warning("git not found; could not restore package-lock.json")
            _record_deps_install(desktop_dir)
        else:
            logger.info("Dependencies unchanged — skipping npm install.")

        proc = await asyncio.create_subprocess_exec(
            "npm", "run", "build",
            cwd=str(desktop_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        if proc.returncode != 0:
            msg = f"npm run build failed (rc={proc.returncode}): {stderr.decode(errors='replace')[-500:]}"
            logger.error(msg)
            return RebuildResult(rebuilt=True, success=False, message=msg)

        logger.info("Desktop bundle rebuilt successfully.")
        # Record provenance so future checks use content hash, not mtime.
        # The marker names HEAD:desktop, which only describes the artefacts we
        # just built when the tree that was built is clean.  After a build from
        # a dirty tree we invalidate the marker instead of writing a SHA the
        # bundle does not correspond to.
        tree_sha = await _get_desktop_tree_sha(project_root)
        if tree_sha and await _is_desktop_working_tree_clean(project_root):
            _record_bundle_provenance(project_root, tree_sha)
        else:
            _clear_bundle_provenance(project_root)
        return RebuildResult(rebuilt=True, success=True, message="Desktop bundle rebuilt successfully.")

    except asyncio.TimeoutError:
        try:
            proc.terminate()
        except Exception:
            pass
        msg = f"Desktop rebuild timed out after {timeout_seconds}s."
        logger.error(msg)
        return RebuildResult(rebuilt=True, success=False, message=msg)

    except FileNotFoundError as exc:
        # npm not on PATH — e.g. minimal Docker image, dev box without Node.
        # This is a benign skip from the caller's perspective.
        msg = f"npm not available — skipping desktop rebuild: {exc}"
        logger.warning(msg)
        return RebuildResult(rebuilt=False, success=True, message=msg)

    except Exception as exc:
        msg = f"Desktop rebuild error: {exc!r}"
        logger.error(msg)
        return RebuildResult(rebuilt=True, success=False, message=msg)
