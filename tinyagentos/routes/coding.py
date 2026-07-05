from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class CreateWorkspaceBody(BaseModel):
    name: str


class WriteFileBody(BaseModel):
    path: str
    content: str


class PathsBody(BaseModel):
    paths: list[str]


class ApplyBlock(BaseModel):
    path: str
    content: str


class ApplyBlocksBody(BaseModel):
    blocks: list[ApplyBlock]


class ToolCallBody(BaseModel):
    name: str
    arguments: dict | None = None


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

async def _git(cwd: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command in *cwd*; return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return (
        proc.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def _invalid_rel_path(rel: str) -> bool:
    if rel.startswith("/") or "://" in rel or rel.startswith("//"):
        return True
    return ".." in Path(rel).parts


_MAX_READ_BYTES = 2_000_000


def _resolve_jailed(root: Path, rel: str, *, allow_root: bool = False) -> Path | None:
    if _invalid_rel_path(rel):
        return None
    target = (root / rel).resolve() if rel else root.resolve()
    if not target.is_relative_to(root):
        return None
    # Never allow paths into the workspace .git directory: writing there (e.g. a
    # hooks/ script) would be code execution on the next git operation.
    if ".git" in target.relative_to(root).parts:
        return None
    if target == root and not allow_root:
        return None
    return target


async def _workspace_root(request: Request, workspace_id: str) -> tuple[Path | None, JSONResponse | None]:
    store = request.app.state.coding_workspaces
    row = await store.get(workspace_id)
    if row is None:
        return None, JSONResponse({"error": "workspace not found"}, status_code=404)
    root = store.workspaces_root.resolve()
    workspace = Path(row["path"]).resolve()
    if not workspace.is_relative_to(root) or workspace == root:
        return None, JSONResponse({"error": "workspace not found"}, status_code=404)
    return workspace, None


@router.post("/api/coding/workspaces")
async def create_workspace(request: Request, body: CreateWorkspaceBody):
    name = (body.name or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    store = request.app.state.coding_workspaces
    try:
        row = await store.create(name)
    except RuntimeError as exc:
        # workspace creation (git init / id allocation) failed. Log the detail
        # (may contain git stderr / internal paths) server-side and return a
        # generic message so nothing internal leaks to the client.
        logger.warning("coding workspace creation failed: %s", exc)
        return JSONResponse({"error": "workspace creation failed"}, status_code=503)
    return row


@router.get("/api/coding/workspaces")
async def list_workspaces(request: Request):
    store = request.app.state.coding_workspaces
    return await store.list()


@router.get("/api/coding/workspaces/{workspace_id}/files")
async def list_files(request: Request, workspace_id: str, subpath: str = ""):
    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err
    target = _resolve_jailed(root, subpath, allow_root=True)
    if target is None:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not target.is_dir():
        return JSONResponse({"error": "not found"}, status_code=404)
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        entries.append({"name": child.name, "is_dir": child.is_dir()})
    return entries


@router.get("/api/coding/workspaces/{workspace_id}/file")
async def read_file(request: Request, workspace_id: str, path: str):
    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err
    target = _resolve_jailed(root, path)
    if target is None:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    if target.stat().st_size > _MAX_READ_BYTES:
        return JSONResponse({"error": "file too large"}, status_code=400)
    try:
        content = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return JSONResponse({"error": "binary_or_undecodable"}, status_code=400)
    return {"path": path, "content": content}


@router.put("/api/coding/workspaces/{workspace_id}/file")
async def write_file(request: Request, workspace_id: str, body: WriteFileBody):
    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err
    rel = body.path or ""
    target = _resolve_jailed(root, rel)
    if target is None:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if target == root or target.is_dir():
        return JSONResponse({"error": "invalid path"}, status_code=400)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content)
    return {"path": rel, "ok": True}


@router.delete("/api/coding/workspaces/{workspace_id}")
async def delete_workspace(request: Request, workspace_id: str):
    store = request.app.state.coding_workspaces
    removed = await store.delete(workspace_id)
    if not removed:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Diff / accept / revert
# ---------------------------------------------------------------------------

@router.get("/api/coding/workspaces/{workspace_id}/diff")
async def workspace_diff(request: Request, workspace_id: str):
    """Return uncommitted changes as a list of {path, status, patch} objects.

    status is one of: added | modified | deleted
    patch is unified diff text (empty string for deleted files with no tracked content).
    """
    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err

    # Collect status of every changed entry (tracked + untracked).
    # Use -z for NUL-separated output so filenames with spaces/quotes are safe.
    rc, out, _e = await _git(root, "status", "--porcelain=v1", "-z")
    if rc != 0:
        return JSONResponse({"error": "git status failed"}, status_code=500)

    entries: list[dict] = []
    # -z output: entries are "<XY> <path>\0"; renames are "<XY> <new>\0<old>\0".
    raw_tokens = out.split("\0")
    status_entries: list[tuple[str, str]] = []
    i = 0
    while i < len(raw_tokens):
        token = raw_tokens[i]
        if len(token) < 4:
            i += 1
            continue
        xy = token[:2]
        rel_path = token[3:]
        if xy[0] in ("R", "C"):
            i += 2  # skip old-path token
        else:
            i += 1
        status_entries.append((xy, rel_path))

    for xy, rel_path in status_entries:
        x, y = xy[0], xy[1]
        untracked = xy == "??"

        if untracked:
            file_status = "added"
        elif x == "D" or y == "D":
            file_status = "deleted"
        elif x == "A" or y == "A":
            file_status = "added"
        else:
            file_status = "modified"

        # Jail the path
        target = _resolve_jailed(root, rel_path)
        if target is None:
            continue

        if untracked:
            # Diff against empty (show full content as +lines)
            if target.is_file():
                try:
                    content = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                lines = content.splitlines(keepends=True)
                patch_lines = [f"+{l}" for l in lines]
                patch = (
                    f"--- /dev/null\n+++ b/{rel_path}\n@@ -0,0 +1,{len(lines)} @@\n"
                    + "".join(patch_lines)
                )
            else:
                patch = ""
        elif file_status == "deleted":
            rc2, patch, _ = await _git(root, "diff", "HEAD", "--", rel_path)
            if rc2 != 0:
                patch = ""
        else:
            rc2, patch, _ = await _git(root, "diff", "HEAD", "--", rel_path)
            if rc2 != 0:
                # May be staged but not yet committed (new index entry)
                rc2, patch, _ = await _git(root, "diff", "--cached", "--", rel_path)
            if rc2 != 0:
                patch = ""

        entries.append({"path": rel_path, "status": file_status, "patch": patch})

    return entries


@router.post("/api/coding/workspaces/{workspace_id}/accept")
async def accept_changes(request: Request, workspace_id: str, body: PathsBody):
    """Stage and commit the given paths (accept the agent's edits)."""
    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err

    safe_paths: list[str] = []
    for p in body.paths:
        target = _resolve_jailed(root, p)
        if target is None:
            return JSONResponse({"error": f"invalid path: {p}"}, status_code=400)
        safe_paths.append(p)

    if not safe_paths:
        return JSONResponse({"error": "no paths provided"}, status_code=400)

    rc, _o, se = await _git(root, "add", "--", *safe_paths)
    if rc != 0:
        logger.warning("git add failed: %s", se)
        return JSONResponse({"error": "git add failed", "detail": se}, status_code=500)

    # Check if anything was actually staged before committing.
    rc_st, st_out, _ = await _git(root, "status", "--porcelain=v1", "-z")
    staged = any(
        len(t) >= 4 and t[0] not in (" ", "?")
        for t in st_out.split("\0")
    )
    if not staged:
        return {"ok": True, "committed": [], "note": "nothing to commit"}

    rc, _o, se = await _git(
        root,
        "-c", "user.name=taOS",
        "-c", "user.email=taos@localhost",
        "commit",
        "-m", f"agent: accept changes to {len(safe_paths)} file(s)",
    )
    if rc != 0:
        logger.warning("git commit failed: %s", se)
        return JSONResponse({"error": "git commit failed", "detail": se}, status_code=500)

    return {"ok": True, "committed": safe_paths}


@router.post("/api/coding/workspaces/{workspace_id}/revert")
async def revert_changes(request: Request, workspace_id: str, body: PathsBody):
    """Discard changes to the given paths (reject the agent's edits)."""
    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err

    safe_paths: list[str] = []
    for p in body.paths:
        target = _resolve_jailed(root, p)
        if target is None:
            return JSONResponse({"error": f"invalid path: {p}"}, status_code=400)
        safe_paths.append(p)

    if not safe_paths:
        return JSONResponse({"error": "no paths provided"}, status_code=400)

    reverted: list[str] = []
    errors: list[str] = []

    # Determine status for each path to decide how to discard
    rc, status_out, _ = await _git(root, "status", "--porcelain=v1", "-z", "--", *safe_paths)

    untracked: set[str] = set()
    tracked: list[str] = []

    raw_tokens = status_out.split("\0")
    i = 0
    while i < len(raw_tokens):
        token = raw_tokens[i]
        if len(token) < 4:
            i += 1
            continue
        xy = token[:2]
        path = token[3:]
        if xy[0] in ("R", "C"):
            i += 2
        else:
            i += 1
        if xy == "??":
            untracked.add(path)
        else:
            tracked.append(path)

    # Tracked: restore via git checkout
    if tracked:
        rc, _o, se = await _git(root, "checkout", "--", *tracked)
        if rc != 0:
            # Try unstaging first (staged new file), then checkout
            await _git(root, "reset", "HEAD", "--", *tracked)
            rc2, _o, se2 = await _git(root, "checkout", "--", *tracked)
            if rc2 != 0:
                errors.extend(tracked)
                logger.warning("git checkout failed for %s: %s", tracked, se2)
            else:
                reverted.extend(tracked)
        else:
            reverted.extend(tracked)

    # Untracked: delete
    for p in safe_paths:
        if p in untracked:
            target = _resolve_jailed(root, p)
            if target and target.exists():
                try:
                    target.unlink()
                    reverted.append(p)
                except OSError as exc:
                    errors.append(p)
                    logger.warning("unlink failed for %s: %s", p, exc)

    if errors:
        return JSONResponse(
            {"ok": False, "reverted": reverted, "failed": errors},
            status_code=207,
        )

    return {"ok": True, "reverted": reverted}


# ---------------------------------------------------------------------------
# Apply agent code blocks
# ---------------------------------------------------------------------------

@router.post("/api/coding/workspaces/{workspace_id}/apply-blocks")
async def apply_blocks(request: Request, workspace_id: str, body: ApplyBlocksBody):
    """Write a batch of files into the workspace, jailed to its directory.

    Each block must supply a relative ``path`` and ``content`` string.
    Paths are validated by ``_resolve_jailed``; any traversal attempt causes
    the entire request to be rejected before any file is written.

    Returns::

        {
            "applied": ["src/App.tsx", ...],
            "skipped": []           # blocks whose path was invalid
        }
    """
    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err

    if not body.blocks:
        return JSONResponse({"error": "no blocks provided"}, status_code=400)

    # Validate all paths before writing anything so a bad path mid-list
    # does not leave the workspace in a partially-written state.
    resolved: list[tuple[str, Path, str]] = []
    for block in body.blocks:
        rel = (block.path or "").strip()
        target = _resolve_jailed(root, rel)
        if target is None or target == root or target.is_dir():
            return JSONResponse(
                {"error": f"invalid path: {block.path!r}"},
                status_code=400,
            )
        resolved.append((rel, target, block.content))

    applied: list[str] = []
    for rel, target, content in resolved:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Defeat a TOCTOU symlink escape: an earlier block in this batch could
        # create a symlink that a later block then writes through. Re-resolve the
        # parent (reveals a symlinked dir pointing outside root) and refuse to
        # follow a symlink at the final component via O_NOFOLLOW.
        if not target.parent.resolve().is_relative_to(root):
            return JSONResponse({"error": f"invalid path: {rel!r}"}, status_code=400)
        try:
            fd = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
            )
        except OSError:
            return JSONResponse({"error": f"invalid path: {rel!r}"}, status_code=400)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        applied.append(rel)

    return {"applied": applied}


# ---------------------------------------------------------------------------
# Agent tool-calling loop (#86)
# ---------------------------------------------------------------------------

@router.get("/api/coding/tools")
async def list_coding_tools(request: Request):
    """The workspace tool schemas the agent loop hands to the model."""
    from tinyagentos.agent_tools.coding_tools import TOOL_SCHEMAS

    return {"tools": TOOL_SCHEMAS}


@router.post("/api/coding/workspaces/{workspace_id}/tool")
async def run_coding_tool(request: Request, workspace_id: str, body: ToolCallBody):
    """Execute one agent tool call against a jailed workspace.

    The execution half of the tool-calling loop: the model proposes a call, the
    controller runs it here and feeds the structured result back. Errors are
    returned as {"ok": false, "error": ...} (HTTP 200) so the loop can recover;
    only an unknown workspace is a hard 404.
    """
    from tinyagentos.agent_tools.coding_tools import dispatch

    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err
    return dispatch(root, body.name, body.arguments)


# ---------------------------------------------------------------------------
# Live preview (#86)
#
# Assembles the workspace's root index.html into a single self-contained HTML
# document by inlining local CSS/JS/image references, so it can be dropped
# straight into a sandboxed iframe (srcDoc) with no further network fetches.
# ---------------------------------------------------------------------------

_MAX_ASSET_BYTES = 2_000_000  # skip inlining any single asset bigger than this
_MAX_PREVIEW_BYTES = 5_000_000  # cap on the total assembled document

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def _is_external_ref(ref: str) -> bool:
    """True for anything that must never be fetched: absolute URLs, protocol-
    relative URLs, data URIs, mailto links, and same-page anchors."""
    ref = ref.strip()
    if not ref:
        return True
    lower = ref.lower()
    return lower.startswith(("http://", "https://", "//", "data:", "mailto:", "#"))


def _strip_query_and_fragment(ref: str) -> str:
    return ref.split("#", 1)[0].split("?", 1)[0]


def _read_local_asset(root: Path, ref: str) -> bytes | None:
    """Read a local, jailed asset relative to *root*. None if unsafe, missing,
    or larger than the per-asset size guard."""
    clean = _strip_query_and_fragment(ref)
    target = _resolve_jailed(root, clean)
    if target is None or not target.is_file():
        return None
    try:
        if target.stat().st_size > _MAX_ASSET_BYTES:
            return None
        return target.read_bytes()
    except OSError:
        return None


def _data_uri(root: Path, ref: str) -> str | None:
    ext = Path(_strip_query_and_fragment(ref)).suffix.lower()
    mime = _MIME_BY_EXT.get(ext)
    if mime is None:
        return None
    data = _read_local_asset(root, ref)
    if data is None:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
# Matches only empty script tags (external `<script src=...></script>`), which
# is all that needs inlining. Inline scripts (a non-empty body) are left
# untouched so they render in the sandbox unchanged.
_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>\s*</script>", re.IGNORECASE)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r'\b(\w+)=(["\'])([^"\']*)\2', re.IGNORECASE)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)


def _assemble_preview_html(root: Path, html: str) -> str:
    remaining = [_MAX_PREVIEW_BYTES - len(html.encode("utf-8"))]

    def consume(n: int) -> bool:
        if n > remaining[0]:
            return False
        remaining[0] -= n
        return True

    def replace_link(m: re.Match) -> str:
        tag = m.group(0)
        attrs = {a: v for a, _q, v in _ATTR_RE.findall(tag)}
        if attrs.get("rel", "").strip().lower() != "stylesheet":
            return tag
        href = attrs.get("href")
        if href is None or _is_external_ref(href):
            return tag
        data = _read_local_asset(root, href)
        if data is None:
            return tag
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return tag
        replacement = f"<style>{text}</style>"
        delta = len(replacement.encode("utf-8")) - len(tag.encode("utf-8"))
        return replacement if consume(delta) else tag

    def replace_script(m: re.Match) -> str:
        tag = m.group(0)
        attrs = {a: v for a, _q, v in _ATTR_RE.findall(tag)}
        src = attrs.get("src")
        if src is None or _is_external_ref(src):
            return tag
        data = _read_local_asset(root, src)
        if data is None:
            return tag
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return tag
        open_tag_m = re.match(r"<script\b([^>]*)>", tag, re.IGNORECASE)
        remaining_attrs = open_tag_m.group(1) if open_tag_m else ""
        remaining_attrs = re.sub(r'\s*\bsrc=["\'][^"\']*["\']', "", remaining_attrs, flags=re.IGNORECASE)
        replacement = f"<script{remaining_attrs}>{text}</script>"
        delta = len(replacement.encode("utf-8")) - len(tag.encode("utf-8"))
        return replacement if consume(delta) else tag

    def replace_img(m: re.Match) -> str:
        tag = m.group(0)
        src_m = re.search(r'\bsrc=(["\'])([^"\']*)\1', tag, re.IGNORECASE)
        if src_m is None:
            return tag
        src = src_m.group(2)
        if _is_external_ref(src):
            return tag
        uri = _data_uri(root, src)
        if uri is None:
            return tag
        replacement = tag[: src_m.start(2)] + uri + tag[src_m.end(2) :]
        return replacement if consume(len(replacement.encode("utf-8")) - len(tag.encode("utf-8"))) else tag

    def replace_style_block(m: re.Match) -> str:
        open_tag, body, close_tag = m.group(1), m.group(2), m.group(3)

        def replace_url(um: re.Match) -> str:
            ref = um.group(2)
            if _is_external_ref(ref):
                return um.group(0)
            uri = _data_uri(root, ref)
            if uri is None:
                return um.group(0)
            replacement = f"url({uri})"
            delta = len(replacement.encode("utf-8")) - len(um.group(0).encode("utf-8"))
            return replacement if consume(delta) else um.group(0)

        new_body = _CSS_URL_RE.sub(replace_url, body)
        return f"{open_tag}{new_body}{close_tag}"

    html = _LINK_TAG_RE.sub(replace_link, html)
    html = _SCRIPT_TAG_RE.sub(replace_script, html)
    html = _IMG_TAG_RE.sub(replace_img, html)
    html = _STYLE_BLOCK_RE.sub(replace_style_block, html)
    return html


@router.get("/api/coding/workspaces/{workspace_id}/preview")
async def preview_workspace(request: Request, workspace_id: str):
    """Assemble a self-contained preview document from the workspace's root
    index.html for the sandboxed preview iframe.

    Only local, relative asset references are inlined (CSS, JS, images/fonts
    via data URIs); every referenced path is jailed with _resolve_jailed, and
    anything external (absolute URL, protocol-relative, data:, mailto:,
    anchor) is left untouched. No network fetches are ever made.
    """
    root, err = await _workspace_root(request, workspace_id)
    if err is not None:
        return err

    index_path = root / "index.html"
    if not index_path.is_file():
        return JSONResponse({"error": "no_index"}, status_code=404)

    try:
        html = index_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return JSONResponse({"error": "no_index"}, status_code=404)

    assembled = _assemble_preview_html(root, html)
    return Response(content=assembled, media_type="text/html")
