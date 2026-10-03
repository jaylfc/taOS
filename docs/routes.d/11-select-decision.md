# Answering a select decision with free text (`other_value`)

<!-- Route module `tinyagentos/routes/decisions.py`. Applies to BOTH answer paths: human `POST /api/decisions/{id}/answer` and agent mirror `POST /api/decisions/{id}/answer/agent` (scope `decisions_write`) -->

## `single_select`

- Send `other_value`, leave `value` empty
- Both → `400` ("cannot combine value with other_value")
- Stored answer = stripped `other_value`

## `multi_select`

- `value` must be list, every element validated against declared options
- Free-text appended: stored = `[*declared_values, other_value.strip()]`
- Non-list `value` → `400`

## Note field

- When present, appended to text routed to agent as `<answer> (note: <note>)`

## Without `other_value`

- Strict validation unchanged: answer must be one of/subset of declared options
- Non-hashable/non-iterable → `400` (fails closed, not `500`)

## Two consequences

- **No per-decision opt-out.** No `allow_other` flag; free-text path on EVERY select decision
- **Agent path gained it too.** Agent with `decisions_write` can record arbitrary free text, not only declared options