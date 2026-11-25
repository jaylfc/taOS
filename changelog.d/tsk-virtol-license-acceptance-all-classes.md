### Added

- License acceptance gate extended to all restrictive `license_class` values: `copyleft`, `non-commercial`, `revenue-capped`, `research-only`, `territory-limited`, `proprietary`, `gated`, `unlicensed` (only `permissive` and `copyleft` remain no-acceptance). Each class shows per-class acceptance text with licence name, version, and link to full text.
- Acceptance record now stores `license_version` (or file hash when no version); a new version requires re-acceptance.
- All catalog manifests with restrictive licences now declare `license_class`, `weights_license`, `license_version`, and `license_url`; a CI test fails if any restrictive manifest is missing `license_class`.
- Territory-limited licences: acceptance dialog plainly states that acceptance never overrides territory exclusions.
- Agent path: agents cannot accept licences on behalf of a person; the consent flow asks the person directly.
