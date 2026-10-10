### Fixed
- Use --append-system-prompt-file instead of --append-system-prompt to avoid E2BIG errors and prevent the system prompt from appearing in process listings.
- The system prompt temp file is now created per turn inside run_turn and removed in a finally, so overlapping turns on the shared harness no longer delete each other's file and a missing claude binary no longer leaves the persona behind in the temp dir.
