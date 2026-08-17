### Fixed
- The deleted-symbols gate now resolves each signal symbol against the merge result before reporting it. Symbols that remain importable at their public path (for example, a module file deleted but shadowed by a same-named package, or a definition moved) are no longer falsely reported as deleted, while genuinely missing symbols and dropped re-exports still fire.
