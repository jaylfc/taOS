### Added
- Canvas drawing recovery endpoints: `GET /api/projects/{project_id}/canvas/elements/{element_id}/original` returns an element's verbatim original payload (including soft-deleted rows), and `GET /api/projects/{project_id}/canvas/legacy?include_deleted=1` lists every row carrying a `tldraw_shape`. Agent-token gating mirrors existing canvas routes.
