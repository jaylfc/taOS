"""Migrate kind=list documents from SharedDocsStore into TodoStore.

One-shot, idempotent migration. Run via the /api/todo/migrate endpoint
(admin-only) or called programmatically.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

logger = logging.getLogger(__name__)

_migration_lock = asyncio.Lock()


async def migrate_list_docs(shared_docs_store, todo_store) -> dict:
    """Migrate all kind=list docs from shared_docs into todo lists.

    Reads every non-archived ``kind=list`` document, creates a corresponding
    todo list with the same owner/title, converts all entries into
    todo items (preserving order, text, done status, and author), then deletes
    the original doc + entries + members from shared_docs.

    Idempotent: newly created todo lists record the source document id in a
    ``migrated_from`` column.  A ``migration_complete`` flag is set only after
    all entries have been successfully copied.  On re-run a source doc is
    matched by its **exact** id rather than by owner+title alone, so two
    source docs that happen to share the same title are never conflated.

    Returns:
        dict with ``migrated`` (lists newly created), ``items`` (total items
        moved), and ``lists`` (list of {old_id, new_id} pairs) for verification.
    """
    async with _migration_lock:
        return await _migrate_list_docs(shared_docs_store, todo_store)


async def _migrate_list_docs(shared_docs_store, todo_store) -> dict:
    """Internal implementation — callers should use migrate_list_docs."""
    list_docs = await shared_docs_store.list_docs_by_kind("list")

    result = {"migrated": 0, "items": 0, "lists": []}

    for doc in list_docs:
        old_id = doc["id"]
        owner = doc["owner_user_id"]
        title = doc.get("title", "")
        entries = doc.get("entries", [])

        existing_lists = await todo_store.list_lists(
            owner, include_archived=True
        )

        # -- idempotency check: exact source-doc match via migrated_from ---
        migrated_match = [
            candidate for candidate in existing_lists
            if candidate.get("migrated_from") == old_id
        ]

        if migrated_match:
            target_id = migrated_match[0]["id"]
            target = await todo_store.get_list(target_id)

            if target and target.get("migration_complete"):
                # Migration was fully completed on a previous run —
                # target is intact, source can be cleaned up.
                logger.info(
                    "todo-migration: skipping already-migrated doc %r "
                    "(owner=%r, title=%r) → existing %r",
                    old_id, owner, title, target_id,
                )
                result["lists"].append(
                    {"old_id": old_id, "new_id": target_id}
                )
                await shared_docs_store.delete_doc(old_id)
                continue

            # Partial migration — target exists with migrated_from stamp
            # but migration_complete was never set.  Resume by re-copying
            # all entries.  Delete any partial items already in the target
            # to avoid duplicates, then re-copy from source.
            logger.info(
                "todo-migration: resuming incomplete migration for "
                "doc %r (owner=%r, title=%r) → existing %r",
                old_id, owner, title, target_id,
            )

            if target is None:
                logger.warning(
                    "todo-migration: target %r for doc %r vanished; "
                    "skipping this run",
                    target_id, old_id,
                )
                continue

            # Clear any partial items already in the target.
            existing_items = target.get("items", [])
            for item in existing_items:
                await todo_store.delete_item(item["id"])

            # Re-copy all entries from source.
            for entry in entries:
                text = entry.get("text", "")
                done = bool(entry.get("done", False))
                author = entry.get("author", "")
                item = await todo_store.add_item(
                    target_id, text, author=author,
                )
                if done:
                    await todo_store.patch_item(item["id"], done=True)

            await todo_store.set_migration_complete(target_id)
            result["items"] += len(entries)
            result["lists"].append(
                {"old_id": old_id, "new_id": target_id}
            )

            # Source cleanup — all entries now live in the target.
            await shared_docs_store.delete_doc(old_id)
            continue

        # -- normal path: create a fresh todo list ------------------------
        try:
            todo_list = await todo_store.create_list(
                owner, title, migrated_from=old_id,
            )
        except sqlite3.IntegrityError:
            logger.warning(
                "todo-migration: concurrent migration detected for "
                "doc %r (owner=%r, title=%r); another process already "
                "claimed it",
                old_id, owner, title,
            )
            continue
        new_id = todo_list["id"]

        # Convert entries to todo items in original order.
        for entry in entries:
            text = entry.get("text", "")
            done = bool(entry.get("done", False))
            author = entry.get("author", "")
            item = await todo_store.add_item(
                new_id, text, author=author,
            )
            if done:
                await todo_store.patch_item(item["id"], done=True)

        await todo_store.set_migration_complete(new_id)
        result["migrated"] += 1
        result["items"] += len(entries)
        result["lists"].append({"old_id": old_id, "new_id": new_id})

        logger.info(
            "todo-migration: migrated list %r → %r (%d items)",
            old_id, new_id, len(entries),
        )

        # Delete the original doc from shared_docs.
        await shared_docs_store.delete_doc(old_id)

    return result
