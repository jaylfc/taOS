"""Library collections handoff — index text artifacts into taosmd collections.

After the ingest pipeline produces text artifacts (extracted text, transcripts,
descriptions), this module hands them off to taosmd collections via the
taosmd HTTP API so agents can query the content through collection grants.

The design doc (docs/design/library-app.md) says:
  "Collections handoff: write text artifacts into a per-target folder under an
  allowed root, then taosmd collections index; link to project; grants stay
  EXPLICIT"

Flow (Phase 1):
  1. Write text artifacts under collections_dir/{item_id}/
  2. Create a collection via POST /collections (taosmd HTTP API)
  3. Trigger async index via POST /collections/{id}/index
  4. Poll GET /collections/{id} until status=ready|error
  5. Link collection to the library item via POST /collections/{id}/link
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

# Text artifact kinds that should be indexed into collections
_TEXT_ARTIFACT_KINDS = frozenset({"text", "transcript", "description", "ocr", "chapters"})

# Maximum polls while waiting for async index to complete.
_MAX_INDEX_POLLS = 30
# Seconds between poll attempts.
_POLL_INTERVAL = 2


async def handoff_to_collections(
    store,
    item_id: str,
    collections_dir: Path,
    taosmd_url: str | None = None,
    taosmd_admin_token: str | None = None,
    project_id: str | None = None,
) -> int:
    """Hand off all text artifacts for an item to taosmd collections.

    Writes text artifacts under ``collections_dir/{item_id}/``, then calls
    the taosmd HTTP API to create a collection and index the text content.

    Returns the number of files indexed (``files_indexed`` from taosmd stats)
    after a successful async index.  Returns 0 when taosmd is unavailable
    (no collection created).

    Parameters
    ----------
    store:
        LibraryStore instance.
    item_id:
        Library item id.
    collections_dir:
        Allowed root for collection files (e.g. ``/opt/taos/data/collections/``).
    taosmd_url:
        Base URL of the running taosmd instance (e.g. ``http://localhost:7900``).
        When omitted or None, file-copy still happens but no collection is
        created or indexed — the caller must have already created the collection
        separately (production paths always supply this; test paths may omit it).
    taosmd_admin_token:
        Admin bearer token for taosmd API auth, fetched from SecretsStore as
        ``taosmd-admin-token``.  Required when *taosmd_url* is set.
    project_id:
        Optional project to link the collection to (Phase 2+).
    """
    artifacts = await store.get_artifacts(item_id)
    if not artifacts:
        return 0

    text_artifacts = [
        a for a in artifacts if a["kind"] in _TEXT_ARTIFACT_KINDS
    ]
    if not text_artifacts:
        return 0

    item = await store.get_item(item_id)
    if not item:
        return 0

    # Write text artifacts to a per-item folder under the collections root
    item_dir = collections_dir / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    # Set group-readable perms on the directory
    try:
        os.chmod(item_dir, stat.S_ISGID | stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
    except OSError:
        pass

    # Copy each text artifact into the collection source path.
    # taosmd discovers files by scanning the source_path directory.
    indexed_paths: list[str] = []
    for art in text_artifacts:
        art_path = art.get("path", "")
        if not art_path:
            continue

        src = Path(art_path)
        if not src.exists():
            continue

        dst = item_dir / src.name
        try:
            raw_bytes = src.read_bytes()
            dst.write_bytes(raw_bytes)
            # Group-readable file perms (0o640 — setgid meaningless on files)
            try:
                os.chmod(dst, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            except OSError:
                pass
        except OSError:
            logger.warning("Failed to copy artifact %s → %s", src, dst,
                           exc_info=True)
            continue
        indexed_paths.append(str(dst))

    if not indexed_paths:
        return 0

    # Index into taosmd collections via the taosmd HTTP API
    if not taosmd_url:
        logger.debug("taosmd URL not provided — collection indexing skipped")
        return 0

    # Parse item metadata once before the httpx try block so exception
    # handlers (below) can merge into it without re-reading a stale item dict.
    item_meta = json.loads(item.get("meta_json", "{}"))

    async def _set_retryable() -> None:
        """Persist collection_retryable so a future retry can re-attempt.

        NOTE: collection_retryable currently has no consumer — it is scaffolding
        for a future retry path. Clearing on success prevents stale markers
        but no code path reads this flag yet.
        """
        try:
            item_meta["collection_retryable"] = True
            await store.update_item(item_id, meta_json=item_meta)
        except Exception:
            pass

    try:
        import httpx

        # Normalise base URL
        base_url = taosmd_url.rstrip("/")

        # Build auth headers when a token is available
        auth_headers: dict[str, str] = {}
        if taosmd_admin_token:
            auth_headers["Authorization"] = f"Bearer {taosmd_admin_token}"

        async with httpx.AsyncClient(timeout=30) as http_client:
            # 1. Get or create a collection for this library item.
            #    Look up a previously-created collection id from item metadata
            #    so reprocess is idempotent (no duplicate collections).
            collection_name = f"library-{item_id[:12]}"
            title = item.get("title", "Untitled") or "Untitled"

            collection_id = ""
            existing_coll_id = item_meta.get("collection_id", "")

            if existing_coll_id:
                # Verify the collection still exists.
                # 404 (genuinely gone) → fall through to create.
                # Transient failure (5xx, timeout, connection error) → mark
                # retryable and bail — must not create a duplicate.
                try:
                    check_resp = await http_client.get(
                        f"{base_url}/collections/{existing_coll_id}",
                        headers=auth_headers,
                    )
                    if check_resp.status_code < 400:
                        collection_id = existing_coll_id
                        logger.debug(
                            "Reusing existing collection %s for item %s",
                            collection_id, item_id,
                        )
                    elif check_resp.status_code == 404:
                        logger.debug(
                            "Existing collection %s not found (404) — "
                            "will create replacement", existing_coll_id,
                        )
                    else:
                        logger.warning(
                            "taosmd GET /collections/%s returned %d — "
                            "transient failure, marking retryable",
                            existing_coll_id, check_resp.status_code,
                        )
                        await _set_retryable()
                        return 0
                except Exception:
                    logger.warning(
                        "taosmd GET /collections/%s failed — "
                        "transient failure, marking retryable",
                        existing_coll_id, exc_info=True,
                    )
                    await _set_retryable()
                    return 0

            if not collection_id:
                source_path = str(item_dir)
                create_resp = await http_client.post(
                    f"{base_url}/collections",
                    json={
                        "name": collection_name,
                        "kind": "mixed",
                        "source_path": source_path,
                        "metadata": {
                            "title": title,
                            "kind": item.get("kind", ""),
                            "source_url": item.get("source_url", ""),
                            "library_item_id": item_id,
                        },
                    },
                    headers=auth_headers,
                )
                if create_resp.status_code >= 400:
                    logger.warning(
                        "taosmd POST /collections returned %d for item %s: %s",
                        create_resp.status_code, item_id, create_resp.text[:200],
                    )
                    await _set_retryable()
                    return 0
                # taosmd 0.4.0 nests the id under "collection"
                collection_id = create_resp.json().get("collection", {}).get("id", "")

                if not collection_id:
                    logger.warning(
                        "taosmd POST /collections returned no collection.id for item %s",
                        item_id,
                    )
                    await _set_retryable()
                    return 0

                # Persist collection_id in item metadata for idempotent reprocess
                item_meta["collection_id"] = collection_id
                await store.update_item(item_id, meta_json=item_meta)

            # 2. Trigger async index — no body, returns 202.
            try:
                index_resp = await http_client.post(
                    f"{base_url}/collections/{collection_id}/index",
                    headers=auth_headers,
                )
                if index_resp.status_code != 202:
                    logger.warning(
                        "taosmd POST /collections/%s/index returned %d (expected 202)",
                        collection_id, index_resp.status_code,
                    )
                    await _set_retryable()
                    return 0
            except Exception:
                logger.warning(
                    "taosmd POST /collections/%s/index failed",
                    collection_id, exc_info=True,
                )
                await _set_retryable()
                return 0

            # 3. Poll until indexing completes.
            indexed = 0
            for _poll_attempt in range(_MAX_INDEX_POLLS):
                try:
                    poll_resp = await http_client.get(
                        f"{base_url}/collections/{collection_id}",
                        headers=auth_headers,
                    )
                    if poll_resp.status_code >= 400:
                        logger.warning(
                            "taosmd GET /collections/%s returned %d during poll",
                            collection_id, poll_resp.status_code,
                        )
                        await _set_retryable()
                        break
                    poll_data = poll_resp.json().get("collection", {})
                    status = poll_data.get("status", "")
                    if status == "ready":
                        stats = poll_data.get("stats", {})
                        indexed = stats.get("files_indexed", 0)
                        logger.info(
                            "Collections handoff for item %s: "
                            "files_indexed=%d files_total=%d "
                            "chunks_ingested=%d chunks_skipped=%d "
                            "collection=%s",
                            item_id,
                            stats.get("files_indexed", 0),
                            stats.get("files_total", 0),
                            stats.get("chunks_ingested", 0),
                            stats.get("chunks_skipped", 0),
                            collection_id,
                        )
                        break
                    elif status == "error":
                        logger.warning(
                            "taosmd collection %s entered error state for item %s",
                            collection_id, item_id,
                        )
                        await _set_retryable()
                        break
                    # Still indexing — wait and retry
                    await asyncio.sleep(_POLL_INTERVAL)
                except Exception:
                    logger.warning(
                        "taosmd poll GET /collections/%s failed",
                        collection_id, exc_info=True,
                    )
                    await _set_retryable()
                    break
            else:
                logger.warning(
                    "taosmd collection %s did not reach ready state within %d polls",
                    collection_id, _MAX_INDEX_POLLS,
                )
                await _set_retryable()

            # 4. Link the collection to the library item.
            try:
                await http_client.post(
                    f"{base_url}/collections/{collection_id}/link",
                    json={"type": "taos", "id": item_id},
                    headers=auth_headers,
                )
                logger.debug(
                    "Linked collection %s to library item %s",
                    collection_id, item_id,
                )
            except Exception:
                logger.warning(
                    "taosmd POST /collections/%s/link failed for item %s",
                    collection_id, item_id, exc_info=True,
                )

            # Clear retryable on success; the handoff completed.
            if indexed > 0:
                item_meta.pop("collection_retryable", None)
                try:
                    await store.update_item(item_id, meta_json=item_meta)
                except Exception:
                    pass

            return indexed

    except ImportError:
        logger.warning("httpx not available — collection indexing skipped")
        await _set_retryable()
    except Exception:
        logger.exception(
            "taosmd collections API unreachable for item %s", item_id,
        )
        await _set_retryable()

    return 0
