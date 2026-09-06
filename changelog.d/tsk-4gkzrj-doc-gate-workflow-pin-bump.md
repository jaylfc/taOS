### Fixed

- `scripts/check_doc_gate.py`: a workflow change that bumps only `uses: <action>@<ref>` pins no longer trips the `contributor-skill` doc-gate rule, so dependency-update PRs for GitHub Actions can turn green on their own instead of stalling red until a maintainer force-pushes a `Docs-Reviewed:` trailer that the bot cannot author. The exemption is content-based: a substantive workflow edit by any author still fails the gate.
