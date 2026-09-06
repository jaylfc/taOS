"""Tests for the kind=list → Todo migration."""

from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.notes.shared_docs_store import SharedDocsStore
from tinyagentos.todo.todo_store import TodoStore
from tinyagentos.todo.migration import migrate_list_docs


@pytest_asyncio.fixture
async def shared_store(tmp_path):
    s = SharedDocsStore(tmp_path / "shared_docs.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def todo_store(tmp_path):
    s = TodoStore(tmp_path / "todo.db")
    await s.init()
    yield s
    await s.close()


# -------------------------------------------------------------------- helpers

async def _setup_list_doc(store, owner, title, entries, done_mask=None):
    """Create a kind=list doc with entries. done_mask is a set of indices."""
    doc = await store.create_doc(owner, "list", title)
    created = []
    for i, text in enumerate(entries):
        entry = await store.add_entry(doc["id"], text, author=owner)
        if done_mask and i in done_mask:
            await store.set_entry_done(entry["id"], True)
        created.append(entry)
    return doc, created


# --------------------------------------------------------------------- tests

@pytest.mark.asyncio
async def test_migrate_empty(shared_store, todo_store):
    """No list docs → zero migrated, idempotent."""
    result = await migrate_list_docs(shared_store, todo_store)
    assert result["migrated"] == 0
    assert result["items"] == 0
    assert result["lists"] == []

    # Idempotent re-run.
    result2 = await migrate_list_docs(shared_store, todo_store)
    assert result2 == result


@pytest.mark.asyncio
async def test_migrate_single_list(shared_store, todo_store):
    """Single list with entries → migrated, source deleted."""
    doc, _entries = await _setup_list_doc(
        shared_store, "user-a", "Groceries",
        ["Milk", "Eggs", "Bread"],
        done_mask={1},
    )

    result = await migrate_list_docs(shared_store, todo_store)
    assert result["migrated"] == 1
    assert result["items"] == 3
    assert len(result["lists"]) == 1
    assert result["lists"][0]["old_id"] == doc["id"]

    new_id = result["lists"][0]["new_id"]

    # Source should be gone.
    assert await shared_store.get_doc(doc["id"]) is None

    # Target should have the list + items.
    todo_list = await todo_store.get_list(new_id)
    assert todo_list is not None
    assert todo_list["owner_user_id"] == "user-a"
    assert todo_list["title"] == "Groceries"
    assert todo_list["archived_at"] is None

    items = todo_list["items"]
    assert len(items) == 3
    assert [i["text"] for i in items] == ["Milk", "Eggs", "Bread"]
    assert [i["done"] for i in items] == [False, True, False]
    assert [i["author"] for i in items] == ["user-a", "user-a", "user-a"]


@pytest.mark.asyncio
async def test_migrate_multiple_lists(shared_store, todo_store):
    """Multiple list docs from different owners → all migrated."""
    doc1, _ = await _setup_list_doc(
        shared_store, "alice", "Weekend",
        ["Clean", "Cook"],
    )
    doc2, _ = await _setup_list_doc(
        shared_store, "bob", "Work",
        ["Report", "Slides", "Email"],
        done_mask={0},
    )

    result = await migrate_list_docs(shared_store, todo_store)
    assert result["migrated"] == 2
    assert result["items"] == 5

    # Both sources deleted.
    assert await shared_store.get_doc(doc1["id"]) is None
    assert await shared_store.get_doc(doc2["id"]) is None

    # Verify data integrity by mapping.
    by_old = {item["old_id"]: item for item in result["lists"]}
    list1 = await todo_store.get_list(by_old[doc1["id"]]["new_id"])
    list2 = await todo_store.get_list(by_old[doc2["id"]]["new_id"])

    assert list1["owner_user_id"] == "alice"
    assert list1["title"] == "Weekend"
    assert [i["text"] for i in list1["items"]] == ["Clean", "Cook"]

    assert list2["owner_user_id"] == "bob"
    assert list2["title"] == "Work"
    assert [i["text"] for i in list2["items"]] == ["Report", "Slides", "Email"]
    assert [i["done"] for i in list2["items"]] == [True, False, False]


@pytest.mark.asyncio
async def test_migrate_skips_notes(shared_store, todo_store):
    """kind=note docs are left untouched by the migration."""
    note = await shared_store.create_doc("user-a", "note", "Ideas")
    await shared_store.add_entry(note["id"], "AI todo app", author="user-a")

    list_doc, _ = await _setup_list_doc(
        shared_store, "user-a", "Tasks",
        ["Do the thing"],
    )

    result = await migrate_list_docs(shared_store, todo_store)
    assert result["migrated"] == 1
    assert result["items"] == 1

    # Note should still exist.
    still_note = await shared_store.get_doc(note["id"])
    assert still_note is not None
    assert still_note["kind"] == "note"
    assert len(still_note["entries"]) == 1
    assert still_note["entries"][0]["text"] == "AI todo app"

    # List should be gone.
    assert await shared_store.get_doc(list_doc["id"]) is None


@pytest.mark.asyncio
async def test_migrate_archived_list_not_migrated(shared_store, todo_store):
    """Archived list docs are excluded from migration."""
    doc, _ = await _setup_list_doc(
        shared_store, "user-a", "Old tasks",
        ["Thing 1"],
    )
    await shared_store.archive_doc(doc["id"])

    result = await migrate_list_docs(shared_store, todo_store)
    assert result["migrated"] == 0
    assert result["items"] == 0

    # Archived doc still exists.
    still_doc = await shared_store.get_doc(doc["id"])
    assert still_doc is not None
    assert still_doc["archived_at"] is not None


@pytest.mark.asyncio
async def test_migrate_idempotent(shared_store, todo_store):
    """Running migration twice should be safe — second call is a no-op."""
    await _setup_list_doc(
        shared_store, "user-a", "Tasks",
        ["A", "B", "C"],
    )

    r1 = await migrate_list_docs(shared_store, todo_store)
    assert r1["migrated"] == 1
    assert r1["items"] == 3

    # Second run — nothing left to migrate.
    r2 = await migrate_list_docs(shared_store, todo_store)
    assert r2["migrated"] == 0
    assert r2["items"] == 0

    # Data still intact in todo store.
    new_id = r1["lists"][0]["new_id"]
    todo_list = await todo_store.get_list(new_id)
    assert todo_list is not None
    assert len(todo_list["items"]) == 3


@pytest.mark.asyncio
async def test_migrate_recovers_from_interruption(shared_store, todo_store):
    """If migration was interrupted after target creation (migrated_from
    stamped) but before items were copied, a re-run detects the incomplete
    target, copies items in, sets migration_complete, and cleans up."""
    doc, _ = await _setup_list_doc(
        shared_store, "user-a", "Interrupted",
        ["Item 1", "Item 2"],
        done_mask={0},
    )

    # Simulate a partial migration: target exists with migrated_from stamp
    # but migration_complete is still 0 (crash before item copy).
    partial = await todo_store.create_list(
        "user-a", "Interrupted", migrated_from=doc["id"],
    )
    partial_id = partial["id"]

    # Run migration — it should detect the incomplete target, copy items,
    # and mark complete.
    result = await migrate_list_docs(shared_store, todo_store)
    assert result["migrated"] == 0  # No new list created
    assert result["items"] == 2     # Entries flowed into existing list
    assert len(result["lists"]) == 1
    assert result["lists"][0]["old_id"] == doc["id"]
    assert result["lists"][0]["new_id"] == partial_id

    # Source should be cleaned up.
    assert await shared_store.get_doc(doc["id"]) is None

    # Exactly one list exists — no duplicate.
    all_lists = await todo_store.list_lists("user-a", include_archived=True)
    matching = [lst for lst in all_lists if lst["title"] == "Interrupted"]
    assert len(matching) == 1

    # Target list now has the items + migrated_from + migration_complete.
    target = await todo_store.get_list(partial_id)
    assert target is not None
    assert target.get("migrated_from") == doc["id"]
    assert target.get("migration_complete") == 1
    assert len(target["items"]) == 2
    assert [i["text"] for i in target["items"]] == ["Item 1", "Item 2"]

    # Idempotent re-run — migration_complete=1, so no duplicate items.
    result2 = await migrate_list_docs(shared_store, todo_store)
    assert result2["items"] == 0
    target2 = await todo_store.get_list(partial_id)
    assert len(target2["items"]) == 2  # still only 2 items


@pytest.mark.asyncio
async def test_migrate_resumes_from_partial_item_copy(
    shared_store, todo_store,
):
    """If migration crashed after copying one item (target stamped,
    migration_complete=0, one item present), resume clears the partial
    items and re-copies all entries without duplicates."""
    doc, _ = await _setup_list_doc(
        shared_store, "user-a", "Partial",
        ["Item A", "Item B", "Item C"],
        done_mask={2},
    )

    # Simulate: target created, one item copied, then crash.
    partial = await todo_store.create_list(
        "user-a", "Partial", migrated_from=doc["id"],
    )
    partial_id = partial["id"]
    await todo_store.add_item(partial_id, "Item A", author="user-a")

    # Run migration — should clear the partial item and re-copy all 3.
    result = await migrate_list_docs(shared_store, todo_store)
    assert result["migrated"] == 0
    assert result["items"] == 3
    assert len(result["lists"]) == 1
    assert result["lists"][0]["new_id"] == partial_id

    # Source deleted.
    assert await shared_store.get_doc(doc["id"]) is None

    # Target has exactly 3 items, no duplicates.
    target = await todo_store.get_list(partial_id)
    assert target.get("migration_complete") == 1
    assert len(target["items"]) == 3
    texts = [i["text"] for i in target["items"]]
    assert texts == ["Item A", "Item B", "Item C"]
    done_flags = [i["done"] for i in target["items"]]
    assert done_flags == [False, False, True]


@pytest.mark.asyncio
async def test_migrate_preserves_done_and_order(shared_store, todo_store):
    """Done flags and entry order are faithfully migrated."""
    _doc, _entries = await _setup_list_doc(
        shared_store, "user-a", "Checklist",
        ["First", "Second", "Third", "Fourth"],
        done_mask={0, 3},  # First and Fourth are done
    )

    result = await migrate_list_docs(shared_store, todo_store)
    new_id = result["lists"][0]["new_id"]
    todo_list = await todo_store.get_list(new_id)

    items = todo_list["items"]
    assert [i["text"] for i in items] == ["First", "Second", "Third", "Fourth"]
    assert [i["done"] for i in items] == [True, False, False, True]
    assert [i["author"] for i in items] == ["user-a"] * 4


@pytest.mark.asyncio
async def test_migrate_same_title_different_docs_no_data_loss(
    shared_store, todo_store,
):
    """Two source docs with the same owner+title must both be fully migrated.

    Regression test for the Kilo finding: matching by owner+title alone can
    match the second source doc to the first doc's target, silently deleting
    the second source without migrating its entries.
    """
    # Two distinct list docs with same owner and same title.
    doc_a, _ = await _setup_list_doc(
        shared_store, "user-a", "Shopping",
        ["Milk", "Eggs"],
    )
    doc_b, _ = await _setup_list_doc(
        shared_store, "user-a", "Shopping",
        ["Bread", "Butter"],
    )

    result = await migrate_list_docs(shared_store, todo_store)

    # Both should be migrated.
    assert result["migrated"] == 2
    assert result["items"] == 4
    assert len(result["lists"]) == 2

    # Both source docs deleted.
    assert await shared_store.get_doc(doc_a["id"]) is None
    assert await shared_store.get_doc(doc_b["id"]) is None

    # Two distinct todo lists, each with correct items.
    all_lists = await todo_store.list_lists("user-a", include_archived=True)
    matching = [lst for lst in all_lists if lst["title"] == "Shopping"]
    assert len(matching) == 2

    items_a = (await todo_store.get_list(matching[0]["id"]))["items"]
    items_b = (await todo_store.get_list(matching[1]["id"]))["items"]
    all_texts = {i["text"] for i in items_a} | {i["text"] for i in items_b}
    assert all_texts == {"Milk", "Eggs", "Bread", "Butter"}

    # Each todo list should be stamped with its source doc id.
    stamped = {lst["id"]: lst.get("migrated_from") for lst in matching}
    assert set(stamped.values()) == {doc_a["id"], doc_b["id"]}


@pytest.mark.asyncio
async def test_migrate_skips_nonempty_user_created_list(
    shared_store, todo_store,
):
    """A user-created todo list (non-empty) with the same title must not be
    used as a recovery target — a new list is created instead."""
    # User has an existing non-empty todo list named "Tasks".
    user_list = await todo_store.create_list("user-a", "Tasks")
    user_list_id = user_list["id"]
    await todo_store.add_item(user_list_id, "Existing item", author="user-a")

    # Now there's a source doc with the same title.
    doc, _ = await _setup_list_doc(
        shared_store, "user-a", "Tasks",
        ["Migrated item 1", "Migrated item 2"],
    )

    result = await migrate_list_docs(shared_store, todo_store)

    # A NEW list should be created (not merged into the user's list).
    assert result["migrated"] == 1
    assert result["items"] == 2

    # Source doc deleted.
    assert await shared_store.get_doc(doc["id"]) is None

    # User's existing list untouched (still has its one item).
    user_list_after = await todo_store.get_list(user_list_id)
    assert user_list_after is not None
    assert len(user_list_after["items"]) == 1
    assert user_list_after["items"][0]["text"] == "Existing item"
    assert user_list_after.get("migrated_from") is None

    # New list has the migrated items + stamp.
    new_id = result["lists"][0]["new_id"]
    new_list = await todo_store.get_list(new_id)
    assert new_list is not None
    assert new_list["title"] == "Tasks"
    assert new_list.get("migrated_from") == doc["id"]
    assert [i["text"] for i in new_list["items"]] == [
        "Migrated item 1", "Migrated item 2",
    ]
    assert new_list["id"] != user_list_id
