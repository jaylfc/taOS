### Added

- An adversarial-verify stage in the static security analysis pipeline: each finding produced by the code analyzers is now re-examined against its source line to refute false positives inside comments, string literals, or known example values before the findings are surfaced to the user.
