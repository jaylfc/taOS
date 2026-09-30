### Added

- Design doc for the skill-collaboration layer (`docs/design/skill-collaboration.md`): the `learning` A2A channel and its subscription/scoping model, the guide-supplement envelope (immutable content-addressed id, attested promotion record, authorized tombstones) and how it merges over the read-only canonical guides without overriding them, per-user (or per-project) isolation and a review-before-spread governance gate built on the Decisions app and the #896 control plane, with a three-slice build plan (#tsk-8801c4b2).

### Changed

- Same doc, lead-review revision: the isolation unit is the **user** (or a project), not the taOS instance, because a multi-user instance hosts several unrelated fleets and "the same taOS instance" is not "a user's own agents"; S2 is now recorded as blocked on the A2A bus redesign (maintainer hold since 2026-08-24), in addition to the isolation question; the section 4 envelope example is now valid JSON (a `//` note inside the fenced block made it unparseable). `tests/test_skill_collaboration_design_doc.py` pins the invariants and rejects the pre-fix wording.
