### Fixed
- Fixed A2A GPU lease epoch-fencing defects (taOS #3594 fix-forward):
  - Re-claim now returns 409 when lease renewal is refused due to epoch mismatch or expiry, instead of posting a claim line and returning success.
  - Release now validates the lease epoch before posting the `[GPU RELEASE]` line, returning 409 if the lease was replaced; also checks the `release_lease` result after posting and returns 409 with `release_refused` status if the release was refused.
  - Added optional `epoch` field to `ReleaseBody` so callers can present their held epoch for fencing, matching the documented behavior in `docs/agent-coordination.md`.