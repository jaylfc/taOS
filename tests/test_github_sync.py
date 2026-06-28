import pytest
import pytest_asyncio

from tinyagentos.projects.task_store import ProjectTaskStore
from tinyagentos.github_sync import (
    issue_marker,
    is_pull_request,
    sync_issues_to_board,
)


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectTaskStore(tmp_path / "tasks.db")
    await s.init()
    yield s
    await s.close()


def _issue(number, title="t", state="open", labels=None, pr=False, body="", url=""):
    d = {
        "number": number,
        "title": title,
        "state": state,
        "labels": labels or [],
        "body": body,
        "html_url": url,
    }
    if pr:
        d["pull_request"] = {"url": "x"}
    return d


def test_is_pull_request():
    assert is_pull_request(_issue(1, pr=True))
    assert not is_pull_request(_issue(1))


@pytest.mark.asyncio
async def test_creates_open_and_closed_cards(store):
    issues = [
        _issue(1, "open one", "open"),
        _issue(2, "closed one", "closed"),
    ]
    res = await sync_issues_to_board(store, "p", issues)
    assert res == {"created": 2, "closed": 1, "reopened": 0, "skipped": 0}
    tasks = await store.list_tasks("p")
    by_marker = {m: t for t in tasks for m in t["labels"] if m.startswith("gh-issue-")}
    assert by_marker[issue_marker(1)]["status"] == "open"
    assert by_marker[issue_marker(2)]["status"] == "closed"


@pytest.mark.asyncio
async def test_skips_pull_requests(store):
    res = await sync_issues_to_board(store, "p", [_issue(5, pr=True)])
    assert res["skipped"] == 1
    assert res["created"] == 0
    assert await store.list_tasks("p") == []


@pytest.mark.asyncio
async def test_idempotent_no_duplicates(store):
    issues = [_issue(1, "x", "open")]
    await sync_issues_to_board(store, "p", issues)
    res = await sync_issues_to_board(store, "p", issues)
    assert res["created"] == 0
    assert len([t for t in await store.list_tasks("p") if issue_marker(1) in t["labels"]]) == 1


@pytest.mark.asyncio
async def test_closing_and_reopening_tracks_issue_state(store):
    await sync_issues_to_board(store, "p", [_issue(1, "x", "open")])
    # issue closed upstream
    res = await sync_issues_to_board(store, "p", [_issue(1, "x", "closed")])
    assert res["closed"] == 1
    card = (await store.list_tasks("p"))[0]
    assert card["status"] == "closed"
    # issue reopened upstream
    res = await sync_issues_to_board(store, "p", [_issue(1, "x", "open")])
    assert res["reopened"] == 1
    assert (await store.list_tasks("p"))[0]["status"] == "open"


@pytest.mark.asyncio
async def test_route_sync_creates_cards_and_persists_repo(client, monkeypatch):
    import tinyagentos.routes.github_sync as gh

    async def fake_fetch(repo, state, token):
        assert repo == "owner/name"
        return [_issue(1, "open issue", "open"), _issue(2, "done", "closed"), _issue(3, pr=True)], False

    monkeypatch.setattr(gh, "_fetch_issues", fake_fetch)
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]

    resp = await client.post(f"/api/projects/{pid}/github/sync", json={"repo": "owner/name"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 2 and body["closed"] == 1 and body["skipped"] == 1
    assert body["truncated"] is False

    # repo is remembered: a second call may omit it
    resp2 = await client.post(f"/api/projects/{pid}/github/sync", json={})
    assert resp2.status_code == 200
    assert resp2.json()["created"] == 0  # idempotent


@pytest.mark.asyncio
async def test_route_requires_repo(client):
    pid = (await client.post("/api/projects", json={"name": "B", "slug": "b"})).json()["id"]
    resp = await client.post(f"/api/projects/{pid}/github/sync", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_route_rejects_malformed_repo(client):
    pid = (await client.post("/api/projects", json={"name": "C", "slug": "c"})).json()["id"]
    for bad in ["../escape", "owneronly", "a/b/c", "owner/na me"]:
        resp = await client.post(f"/api/projects/{pid}/github/sync", json={"repo": bad})
        assert resp.status_code == 400, bad


@pytest.mark.asyncio
async def test_route_reports_truncation(client, monkeypatch):
    import tinyagentos.routes.github_sync as gh

    async def fake_fetch(repo, state, token):
        return [_issue(1, "x", "open")], True

    monkeypatch.setattr(gh, "_fetch_issues", fake_fetch)
    pid = (await client.post("/api/projects", json={"name": "D", "slug": "d"})).json()["id"]
    resp = await client.post(f"/api/projects/{pid}/github/sync", json={"repo": "o/n"})
    assert resp.status_code == 200
    assert resp.json()["truncated"] is True


@pytest.mark.asyncio
async def test_route_unknown_project_404(client):
    resp = await client.post("/api/projects/prj-missing/github/sync", json={"repo": "o/n"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_issue_labels_and_url_carried_onto_card(store):
    issues = [_issue(1, "x", "open", labels=[{"name": "bug"}, "perf"], body="do it", url="http://gh/1")]
    await sync_issues_to_board(store, "p", issues)
    card = (await store.list_tasks("p"))[0]
    assert "github" in card["labels"] and "bug" in card["labels"] and "perf" in card["labels"]
    assert "http://gh/1" in card["body"]
