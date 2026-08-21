# Answering a select decision with free text (`other_value`)

<!-- Route module `tinyagentos/routes/decisions.py`. Applies to BOTH answer paths: human `POST /api/decisions/{id}/answer` and the agent mirror `POST /api/decisions/{id}/answer/agent` (scope `decisions_write`) -->

## `single_select`

- Send `other_value` and leave `value` empty
- Sending both is a `400` ("cannot combine value with other_value")
- The stored answer is the stripped `other_value`

## `multi_select`

- `value` must still be a list and **every element is still validated against the declared options**
- The free-text entry is appended, so the stored answer is `[*declared_values, other_value.strip()]`
- A non-list `value` is a `400`

## Note field

- When present it is appended to the text routed to the agent as `<answer> (note: <note>)`

## Without `other_value`

- The original strict validation is unchanged: the answer must be one of, or a subset of, the declared options
- A non-hashable or non-iterable value fails closed as `400` rather than `500`

## Two consequences

- **There is no per-decision opt-out.** No `allow_other` flag exists, so the free-text path is available on EVERY select decision
- **The agent path gained it too.** An agent holding `decisions_write` can record arbitrary free text where it was previously constrained to the declared options