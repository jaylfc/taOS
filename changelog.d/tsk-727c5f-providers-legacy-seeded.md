### Fixed

- GET /api/providers now reports legacy provider entries (saved before the `models_seeded` marker existed) whose stored models equal the type seed list as `models_seeded: true`, so onboarding no longer treats an unproven key as working.
