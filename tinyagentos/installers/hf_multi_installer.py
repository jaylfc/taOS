"""HuggingFace multi-file repo installer.

For backends that ship a model as a directory of files rather than a single
download (MLC-LLM compiled artefacts, multi-file diffusers / transformers
checkpoints, etc.) this installer:

1. Lists every file in the HF repo via the public API (no auth required for
   public repos; gated repos would need a token, surfaced separately).
2. Downloads each one to ``~/models/<backend>/<family>/<manifest_id>/<rel_path>``
   preserving the repo's directory structure so consumers like ``mlc_chat``
   or ``transformers.from_pretrained()`` find the files in the layout they
   expect.
3. Reports progress as bytes-downloaded across the whole repo so the UI's
   install bar moves smoothly instead of resetting per file.

Manifest variant fields:

    hf_repo: mlc-ai/Llama-3-8B-Instruct-q4f16_1-MLC   # required
    hf_revision: main                                 # optional, default "main"
    multi_file: true                                  # required marker
    exclude_patterns: ["*.md", ".gitattributes"]      # optional glob blocklist

Single-file fallback: a variant with ``download_url`` and no ``hf_repo`` is
delegated back to the regular DownloadInstaller so callers don't have to
care which path applies.
"""
from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any

import httpx

from tinyagentos.installers.base import AppInstaller
from tinyagentos.installers.download_installer import download_file
from tinyagentos.installers.model_paths import (
    backend_model_dir,
    family_from_manifest,
    models_root,
)

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co/api/models/{repo}"
HF_FILE = "https://huggingface.co/{repo}/resolve/{rev}/{path}"

DEFAULT_EXCLUDE_PATTERNS = (
    ".gitattributes",
    "README.md",
    "LICENSE",
    "*.md",
    ".gitignore",
)


async def list_hf_repo_files(
    repo: str, revision: str = "main", *, client: httpx.AsyncClient | None = None
) -> list[dict]:
    """Fetch the file manifest for an HF repo.

    Returns a list of ``{rfilename, size, lfs}`` dicts. ``size`` is bytes
    when known; ``lfs`` indicates whether the file is stored via Git LFS
    (large file).
    """
    own_client = client is None
    c = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        params = {"revision": revision} if revision and revision != "main" else None
        resp = await c.get(HF_API.format(repo=repo), params=params)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own_client:
            await c.aclose()
    siblings = data.get("siblings") or []
    out: list[dict] = []
    for s in siblings:
        if not isinstance(s, dict):
            continue
        rfilename = s.get("rfilename")
        if not rfilename:
            continue
        # Some HF entries return size as None, missing, or a string. Coerce
        # defensively — failing the whole listing because one sibling has a
        # weird size field would be a poor reason to abort an install.
        raw_size = s.get("size", 0)
        try:
            size = int(raw_size) if raw_size is not None else 0
        except (TypeError, ValueError):
            size = 0
        out.append({
            "rfilename": rfilename,
            "size": size,
            "lfs": bool(s.get("lfs")) if "lfs" in s else False,
        })
    return out


def _safe_relative_path(rfilename: str) -> Path | None:
    """Reject any HF rfilename that would resolve outside the target dir.

    HF repos shouldn't ever contain absolute paths or ``..`` segments, but
    a malicious / corrupted manifest could. We always join the rfilename
    onto ``target_dir`` and verify the resolved path is still inside; if
    not, return None so the caller can skip and log.
    """
    if not rfilename or rfilename.startswith("/"):
        return None
    p = Path(rfilename)
    if p.is_absolute():
        return None
    # Reject any segment that's literal '..' — Path.parts after Path()
    # construction preserves these for traversal-detection purposes.
    if any(part == ".." for part in p.parts):
        return None
    return p


def _file_excluded(rfilename: str, patterns: list[str]) -> bool:
    name = rfilename.lstrip("/")
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(Path(name).name, pat):
            return True
    return False


class HFMultiInstaller(AppInstaller):
    """Install a multi-file HuggingFace repo into the shared model layout."""

    def __init__(self, *, _root_override: Path | None = None):
        # Tests can pass a tmp_path here so the installer doesn't write into
        # the real ~/models. Production callers leave it None and the path
        # comes from model_paths.models_root() (TAOS_MODELS_ROOT env override).
        self._root_override = Path(_root_override) if _root_override else None

    def _backend_id(self, install_config: dict) -> str:
        backend = install_config.get("backend") if install_config else None
        return str(backend) if backend else "huggingface"

    def _target_dir(self, backend_id: str, app_id: str) -> Path:
        if self._root_override is not None:
            return self._root_override / backend_id / family_from_manifest(app_id) / app_id
        return backend_model_dir(backend_id, app_id)

    async def install(
        self,
        app_id: str,
        install_config: dict,
        variant: dict | None = None,
        **kwargs: Any,
    ) -> dict:
        if not variant:
            return {"success": False, "error": "variant required"}

        repo = variant.get("hf_repo")
        if not repo:
            # Single-file fallback for variants that came through the
            # huggingface route by accident — delegate.
            from tinyagentos.installers.download_installer import DownloadInstaller
            return await DownloadInstaller().install(
                app_id, install_config, variant=variant, **kwargs
            )

        revision = variant.get("hf_revision", "main")
        backend_id = self._backend_id(install_config or {})
        target_dir = self._target_dir(backend_id, app_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        on_progress = kwargs.get("on_progress")
        # Be defensive about caller-supplied filter lists: a manifest author
        # could write ``exclude_patterns: "*.bak"`` (a string) instead of a
        # list. ``list.extend(str)`` would then add individual chars to the
        # blocklist rather than treating it as a single pattern.
        excludes: list[str] = list(DEFAULT_EXCLUDE_PATTERNS)
        raw_excludes = variant.get("exclude_patterns") or []
        if isinstance(raw_excludes, str):
            raw_excludes = [raw_excludes]
        if isinstance(raw_excludes, list):
            excludes.extend(p for p in raw_excludes if isinstance(p, str))
        raw_includes = variant.get("include_patterns") or []
        if isinstance(raw_includes, str):
            raw_includes = [raw_includes]
        includes: list[str] = (
            [p for p in raw_includes if isinstance(p, str)]
            if isinstance(raw_includes, list)
            else []
        )

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as api:
                files = await list_hf_repo_files(repo, revision, client=api)
        except httpx.HTTPError as exc:
            return {
                "success": False,
                "error": f"failed to list files for {repo!r}: {exc}",
            }

        # Filter
        selected: list[dict] = []
        for f in files:
            if _file_excluded(f["rfilename"], excludes):
                continue
            if includes and not any(fnmatch.fnmatch(f["rfilename"], p) for p in includes):
                continue
            selected.append(f)

        if not selected:
            return {
                "success": False,
                "error": f"no files matched after filtering for {repo!r}",
            }

        total_bytes = sum(f["size"] for f in selected) or 0
        downloaded_bytes = 0

        # Per-file callback aggregates into the cross-repo total so the UI
        # bar progresses linearly rather than resetting on each file. Track
        # per-file last value and add deltas to the cumulative count.
        per_file_prev: dict[str, int] = {}

        def _make_cb(rfilename: str):
            def _cb(local_done: int, local_total: int) -> None:
                nonlocal downloaded_bytes
                prev = per_file_prev.get(rfilename, 0)
                if local_done < prev:
                    return  # never go backwards
                downloaded_bytes += local_done - prev
                per_file_prev[rfilename] = local_done
                if on_progress is not None:
                    try:
                        on_progress(downloaded_bytes, total_bytes)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "hf-multi: on_progress raised %s, continuing", exc,
                        )
            return _cb

        # Resolve target_dir once for boundary checks below.
        target_resolved = target_dir.resolve()

        for f in selected:
            rfilename = f["rfilename"]
            file_size = f["size"]

            # Path traversal guard: HF rfilenames should always be repo-
            # relative, but a hostile API response (or a corrupted cache)
            # could include "/etc/passwd" or "../etc/passwd". Reject any
            # rfilename that's absolute, contains '..' segments, or
            # resolves outside target_dir after joining.
            safe_rel = _safe_relative_path(rfilename)
            if safe_rel is None:
                logger.warning(
                    "hf-multi: skipping unsafe rfilename %r in repo %r",
                    rfilename, repo,
                )
                continue
            local = (target_dir / safe_rel)
            try:
                if not local.resolve().is_relative_to(target_resolved):
                    logger.warning(
                        "hf-multi: %r resolves outside %s — skipping",
                        rfilename, target_resolved,
                    )
                    continue
            except (OSError, ValueError):
                # is_relative_to is 3.9+; fall back to string-prefix check
                if not str(local.resolve()).startswith(str(target_resolved)):
                    continue

            url = HF_FILE.format(repo=repo, rev=revision, path=rfilename)
            if local.exists():
                # Skip files we've already downloaded — sha verification
                # not in this iteration; HF resolve URLs are immutable per
                # revision so a present file is a present file. Surface
                # the byte count anyway so the aggregate progress is right.
                downloaded_bytes += file_size
                per_file_prev[rfilename] = file_size
                if on_progress is not None:
                    try:
                        on_progress(downloaded_bytes, total_bytes)
                    except Exception:  # noqa: BLE001
                        pass
                continue

            try:
                await download_file(
                    url,
                    local,
                    expected_sha256=None,
                    on_progress=_make_cb(rfilename),
                )
            except Exception as exc:  # noqa: BLE001
                # Leave partially downloaded files alone — caller can
                # retry. Single bad file aborts the whole install.
                return {
                    "success": False,
                    "error": f"download failed for {repo}/{rfilename}: {exc}",
                    "downloaded_bytes": downloaded_bytes,
                    "target_dir": str(target_dir),
                }

        return {
            "success": True,
            "app_id": app_id,
            "target_dir": str(target_dir),
            "files_downloaded": len(selected),
            "bytes_downloaded": downloaded_bytes,
        }

    async def uninstall(self, app_id: str, variant_id: str | None = None, **kwargs) -> dict:
        # Walk every backend root for this app's manifest dir. Each unlink
        # is wrapped — a single locked file (e.g. mmap'd by a running
        # llama-server) shouldn't fail the whole uninstall and leave the
        # registry inconsistent. Surface failures in the result so the
        # caller can decide whether to retry or escalate.
        family = family_from_manifest(app_id)
        root = self._root_override if self._root_override is not None else models_root()
        if not root.exists():
            return {"success": True, "deleted": [], "failed": []}

        deleted: list[str] = []
        failed: list[dict] = []
        for backend_dir in sorted(root.iterdir()):
            if not backend_dir.is_dir():
                continue
            manifest_dir = backend_dir / family / app_id
            if not manifest_dir.exists():
                continue
            for f in sorted(manifest_dir.rglob("*")):
                if not f.is_file():
                    continue
                rel = str(f.relative_to(manifest_dir))
                try:
                    f.unlink()
                    deleted.append(rel)
                except OSError as exc:
                    failed.append({"path": rel, "error": str(exc)})
            # Remove empty dirs (deepest first). Best-effort.
            for d in sorted(manifest_dir.rglob("*"), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass
            try:
                manifest_dir.rmdir()
            except OSError:
                pass

        return {
            "success": not failed,
            "deleted": deleted,
            "failed": failed,
        }
