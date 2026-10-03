# Adding a note to a decision

<!-- Route module `tinyagentos/routes/decisions.py`. Applies to both human sessions and device bearers -->

## `POST /api/decisions/{decision_id}/note`

Body:
```json
{
  "text": "string (required, non-empty after strip)",
  "source": "in_app"
}
```

- `text` is REQUIRED and must be non-empty after strip -> 400 otherwise. No default.
- Ownership: the caller must own the decision or be an admin -> 404 otherwise. Reuses the same ownership rule as `answer_decision`.
- A device bearer MAY post a note on ANY decision INCLUDING a gate-kind one. A note carries no grant, so the phone notification-surface restriction that applies to `answer_decision` does not apply here.
- Does NOT change `status`, `answer`, or `answered_at`.
- Allowed on already-answered or superseded decisions (a note is commentary, not a state transition).
- Returns the updated decision with `notes` populated (oldest first).

## Response

The updated decision dict, identical shape to `GET /api/decisions/{id}`, with `notes` appended.

## Live update

Publishes a `decision.note` event on the owner's `user:<id>` channel so open surfaces refresh without manual reload.
