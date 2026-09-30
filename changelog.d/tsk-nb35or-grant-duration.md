### Fixed

- Agent consent cards and notifications now display human-readable grant duration bounds (e.g., "expires 1 hour after approval", "no expiry") when duration_secs is set on auth requests, making it clear to approvers how long the grant will last. This addresses the issue where unbounded grants and bounded grants were indistinguishable in the UI.
- Duration formatting now consistently uses "after approval" wording for all units (minutes, hours, days), omits zero-minute parts (e.g., 3601s shows "expires 1 hour after approval" not "1 hour 0 minutes"), and uses "expires in under a minute" for 1-59 seconds.

- Updated backend notification messages and auth-request status responses to include human-readable duration information
- Updated frontend ConsentActions component to display the human-readable duration in the consent UI
- Updated frontend AuthRequestCard component in the Decisions app to display grant duration information
