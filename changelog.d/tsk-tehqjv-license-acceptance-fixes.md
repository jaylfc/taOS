### Fixed
- License acceptance text now correctly interpolates the license URL (f-string fix for class_text entries).
- Agent license guard now rejects all agent credential paths (local_token, registry_jwt_deployed_agent, registry_jwt_native_agent, device_bearer, device_bearer_candidate), not just local_token.
- Territory-limited licenses no longer use the spoofable cf-ipcountry header; territory must come from the user's explicitly-set profile country field.
- License acceptance history migration now properly recreates the table to drop the old UNIQUE constraint, preserving history when upgrading from schemas that lacked license_version in the unique key.
- Added upgrade test for license acceptance store migration.
