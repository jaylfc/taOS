### Fixed
- `POST /api/decisions/{id}/answer` now rejects an empty list for `multi_select` answers with a 400. Previously an empty list was accepted silently, recording the decision as answered while carrying no selection and making it indistinguishable downstream from a real choice.
