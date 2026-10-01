# Dependency licence inventory

taOS ships under AGPL-3.0-or-later only (`LICENSE`); there is no commercial
licence. A dependency is acceptable when its licence is compatible with
AGPL-3.0-or-later as shipped.

This file records the licences that needed a decision — not every transitive
package. Add a row whenever a new dependency is dual-licensed, is copyleft, or
offers an extra that would pull in something copyleft.

## Recorded elections

| Package | Licence | Election / decision |
| --- | --- | --- |
| `python-slugify` (core) | MIT | Accepted as-is. |
| `text-unidecode` (via `python-slugify`) | Artistic-1.0 **OR** GPL-2.0-or-later | **We elect the Artistic-1.0 arm.** It is permissive and compatible with the AGPL. This election is deliberate and must survive any dependency refresh. |
| `pypdf` (core) | BSD-3-Clause | Accepted as-is. Pure Python, permissive, no copyleft concerns. |

## Blocked

| Package | Licence | Why |
| --- | --- | --- |
| `Unidecode` (i.e. the `python-slugify[unidecode]` extra) | GPL-2.0-or-later, **no permissive arm** | Kept out by choice: `text-unidecode` covers the need with a permissive licence, so there is no reason to ship a GPL-only transliterator. (It was a hard blocker while a commercial licence existed; under AGPL-only it is compatible, but the rule stays.) Never add the `unidecode` extra to `python-slugify`, and never depend on `Unidecode` directly. `python-slugify` uses `text-unidecode` when the extra is absent, which is what we want. |

`tests/test_config_slugify.py::TestTheGplUnidecodeIsNeverInstalled` enforces the
blocked row mechanically against `pyproject.toml` and `uv.lock`, so the rule
fails CI rather than relying on a reviewer noticing it.
