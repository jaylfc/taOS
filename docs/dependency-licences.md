# Dependency licence inventory

taOS ships under a dual licence: AGPL-3.0 for the public core and a separate
commercial licence (`LICENSE`, `COMMERCIAL-LICENSE.md`). A dependency is only
acceptable if it is usable under **both** arms. A copyleft dependency that is
fine under AGPL can still be a blocker for the commercial arm, so the rule is
stricter than "compatible with the licence in `LICENSE`".

This file records the licences that needed a decision — not every transitive
package. Add a row whenever a new dependency is dual-licensed, is copyleft, or
offers an extra that would pull in something copyleft.

## Recorded elections

| Package | Licence | Election / decision |
| --- | --- | --- |
| `python-slugify` (core) | MIT | Accepted as-is. |
| `text-unidecode` (via `python-slugify`) | Artistic-1.0 **OR** GPL-2.0-or-later | **We elect the Artistic-1.0 arm.** It is permissive and usable under both the AGPL core and the commercial licence. This election is deliberate and must survive any dependency refresh. |

## Blocked

| Package | Licence | Why |
| --- | --- | --- |
| `Unidecode` (i.e. the `python-slugify[unidecode]` extra) | GPL-2.0-or-later, **no permissive arm** | A blocker for the commercial licence. Never add the `unidecode` extra to `python-slugify`, and never depend on `Unidecode` directly. `python-slugify` uses `text-unidecode` when the extra is absent, which is what we want. |

`tests/test_config_slugify.py::TestTheGplUnidecodeIsNeverInstalled` enforces the
blocked row mechanically against `pyproject.toml` and `uv.lock`, so the rule
fails CI rather than relying on a reviewer noticing it.
