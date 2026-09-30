### Added

- `docs/design/cross-node-summarization.md`: design spike for taOS #156 — idle CPU nodes compress
  old conversation segments in the background. Covers node eligibility (derived from worker status,
  GPU leases and idleness), how summaries are produced, stored and pulled back through the RAG path,
  stale-summary vs live-context consistency rules, and a three-slice plan (local compression behind
  an idle gate, cross-node dispatch, live-context splice) with per-slice acceptance criteria.
  Design only — no behaviour change in this PR.
- Follows the #3225 Lead review: the summary row identity is owner-scoped (`user_id` in the store
  primary key, the vector `metadata_json` and both lookup tiers, since agent names are not unique
  under per-user namespacing), and the "default on or default off" choice is recorded as an open
  product question instead of being assumed.
