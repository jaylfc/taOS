### Fixed
- Subagent worker exceptions are now propagated through `await_subagent` instead of being swallowed; a failed subagent raises the original exception at the caller, making failures observable rather than indistinguishable from success.
