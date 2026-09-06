"""Sync GitHub issues onto a project board as cards.

One-way, idempotent: each issue maps to exactly one card, keyed by a
``gh-issue-{number}`` label. Re-running updates state instead of duplicating.
Pull requests are skipped on purpose: PRs are implementation artifacts that link
to a card via the ``exec/<task-id>`` branch convention, not work items in their
own right. The upsert logic here is pure (it takes a list of issue dicts), so it
is fully testable without network access; the HTTP fetch lives in the route.
"""

from __future__ import annotations

GH_LABEL_PREFIX = "gh-issue-"
SYNC_ACTOR = "github-sync"


def issue_marker(number: int) -> str:
    return f"{GH_LABEL_PREFIX}{number}"


def is_pull_request(issue: dict) -> bool:
    # GitHub's issues API returns PRs too; they carry a 'pull_request' key.
    return "pull_request" in issue


def _issue_label_names(issue: dict) -> list[str]:
    out = []
    for lab in issue.get("labels") or []:
        name = lab.get("name") if isinstance(lab, dict) else lab
        if name:
            out.append(str(name))
    return out


async def sync_issues_to_board(
    task_store,
    project_id: str,
    issues: list[dict],
    created_by: str = SYNC_ACTOR,
) -> dict:
    """Upsert GitHub issues as board cards. Returns a counts summary.

    - new open issue  -> create an open card
    - new closed issue -> create a card and close it
    - issue closed since last sync -> close the existing card
    - issue reopened since last sync -> reopen the existing card
    - pull requests -> skipped
    """
    existing = await task_store.list_tasks(project_id)
    by_marker: dict[str, dict] = {}
    for t in existing:
        for lab in t.get("labels") or []:
            if lab.startswith(GH_LABEL_PREFIX):
                by_marker[lab] = t

    created = closed = reopened = skipped = 0
    for issue in issues:
        if is_pull_request(issue):
            skipped += 1
            continue
        num = issue.get("number")
        if num is None:
            skipped += 1
            continue
        marker = issue_marker(int(num))
        state = (issue.get("state") or "open").lower()
        card = by_marker.get(marker)

        if card is None:
            body = (issue.get("body") or "").strip()
            url = issue.get("html_url") or ""
            full_body = (body + ("\n\n" if body and url else "") + url).strip()
            labels = ["github", marker, *_issue_label_names(issue)]
            new = await task_store.create_task(
                project_id=project_id,
                title=issue.get("title") or f"Issue #{num}",
                created_by=created_by,
                body=full_body,
                labels=labels,
            )
            created += 1
            if state == "closed":
                if await task_store.close_task(new["id"], closed_by=created_by, reason="issue closed on GitHub"):
                    closed += 1
            continue

        status = card.get("status")
        if state == "closed" and status != "closed":
            if await task_store.close_task(card["id"], closed_by=created_by, reason="issue closed on GitHub"):
                closed += 1
        elif state == "open" and status == "closed":
            if await task_store.reopen_task(card["id"], reopened_by=created_by):
                reopened += 1

    return {"created": created, "closed": closed, "reopened": reopened, "skipped": skipped}
