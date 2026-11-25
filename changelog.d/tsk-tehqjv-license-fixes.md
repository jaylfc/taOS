### Fixed
- License acceptance text now correctly renders the license URL (f-string prefix on all lines)
- License class allowlist enforced: unknown license classes are not gated (catalog validation rejects them at load time)
- Territory-limited licenses only trust user profile country; spoofable headers (CF-IPCountry) are never used
- Agent guard blocks all agent paths (bound local tokens, deployed/native agents, registry JWT candidates) from accepting licenses
- License acceptance history preserved: new license versions add rows; old UNIQUE constraint migrated safely
