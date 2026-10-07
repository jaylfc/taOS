### Fixed

- Added proof requirement for reusing existing active agent identities when adding new projects (taOS #1862). When an agent already has an active identity and requests to add it to another project with project-scoped grants, they must now present their agent's registry token (Bearer header) to prove they are the same agent. Without proof or with wrong proof, the approval returns 409 with instructions to re-request with the correct token.

- Added `proven_canonical_id` nullable column to auth_requests table to track whether an auth request was created with proof of agent identity

- Enhanced existing tests to include proof validation scenarios:
  - `test_reuse_active_handle_adds_second_project` now includes `proven_canonical_id` to prove the reuse works WITH proof
  - `test_same_origin_reapproval_reuses_existing_identity` updated to include `proven_canonical_id` to maintain test consistency

- Added new tests for proof validation:
  - `test_reuse_without_proof_is_409`: Tests that second request without proof fails with 409
  - `test_reuse_with_wrong_proof_is_409`: Tests that wrong proof fails with 409
  - `test_create_route_records_bearer_identity`: Tests that Bearer token authentication is properly recorded

- Added test for database migration: `test_existing_db_gains_proven_canonical_id_column` ensures existing databases gain the column on init