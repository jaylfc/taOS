### Fixed

- POST /api/providers now returns a `models_source` field on the added provider: `discovered` when models came from a live /models probe, `defaults` when the per-type seed list was used, `none` when no seed exists for the type, or `supplied` when the caller supplied its own models. `models_source` identifies the source of the model list; the seed defaults apply when the probe returns no models.
