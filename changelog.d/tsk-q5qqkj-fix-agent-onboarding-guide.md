### Fixed

- `docs/agent-onboarding.md` freshness-cron section rewritten to state fleet HOLD (crons stopped, no re‑arm, manual sweep)
- identity rule reordered: `@taOS` is the PROJECT identity; every post uses the current seat's registry identity
- canonical task list rewritten: project board `prj-5y722y` is canonical store, GitHub issues are auxiliary
- `_extract_doc_paths` filter removed so root-level `.md` references are extracted and validated