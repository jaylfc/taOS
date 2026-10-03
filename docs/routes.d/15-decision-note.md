# Adding a note to a decision

<!-- Route module `tinyagentos/routes/decisions.py`. Applies to both human sessions and device bearers -->

## `POST /api/decisions/{decision_id}/note`

Body:
```json
{"text": "string (required, non-empty after strip)", "source": "in_app"}
```

- `text` REQUIRED, non-empty after strip → `400`. No default.
- Ownership: caller must own decision or be admin → `404`. Same rule as `answer_decision`.
- Device bearer MAY post note on ANY decision INCLUDING gate-kind. Note carries no grant, so phone notification restriction on `answer_decision` doesn't apply.
- Does NOT change `status`, `answer`, `answered_at`.
- Allowed on answered/superseded decisions (note = commentary, not state transition).
- Returns updated decision with `notes` (oldest first).

## Response

Updated decision dict, same shape as `GET /api/decisions/{id}`, with `notes` appended.

## Live update

Publishes `decision.note` on owner's `user:<id>` channel for live refresh.