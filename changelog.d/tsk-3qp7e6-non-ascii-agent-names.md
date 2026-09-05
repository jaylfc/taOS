### Fixed

- Agents can be named in Chinese, Japanese, Korean, Cyrillic, Greek, Arabic, Hebrew or Thai. Such a name was rejected with "Agent name must contain at least one letter or number" because the slugifier deleted every non-ASCII character before checking whether anything was left; names are now transliterated, so each gets its own distinct slug. Accents fold to their base letter instead of being dropped ("naïve résumé" was `na-ve-r-sum`, now `naive-resume`).
- Two agents whose names produced no slug no longer share an identity prefix in the agent registry. The `"agent"` fallback gave every such name the same slug; the fallback is now derived from the name itself, so the canonical ids stay distinct. Creating a project from a consent request has the same fix.
- Deduplicating an already-63-character agent slug no longer overruns the container-name limit the truncation exists to respect.
