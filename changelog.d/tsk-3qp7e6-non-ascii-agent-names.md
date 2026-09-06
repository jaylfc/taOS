### Fixed

- Agents can be named in Chinese, Japanese, Korean, Cyrillic, Greek, Arabic, Hebrew or Thai. Such a name was rejected with "Agent name must contain at least one letter or number" because the slugifier deleted every non-ASCII character before checking whether anything was left; names are now transliterated, so each gets its own distinct slug. Accents fold to their base letter instead of being dropped ("naïve résumé" was `na-ve-r-sum`, now `naive-resume`).
- Two agents whose names produced no slug no longer share an identity prefix in the agent registry. The `"agent"` fallback gave every such name the same slug; the fallback is now derived from the name itself, so the canonical ids stay distinct. Creating a project from a consent request has the same fix.
- Deduplicating an already-63-character agent slug no longer overruns the container-name limit the truncation exists to respect.
- The unslugifiable-name fallback slug uses a wider digest (8 bytes instead of 4), closing a collision window that could resolve a slug lookup (e.g. a DM channel member) to the wrong agent.
- The OS-level project-invite redeem handle no longer drops the harness when the display name is unslugifiable but the label slugifies -- two different harnesses no longer collide on the same label-only handle.
- The Deploy and Import wizards no longer claim taOS will derive a slug for a name with no Latin letters; those two forms reject such a name server-side (no fallback), so the hint now points at the manual "edit" control instead.
