### Fixed
- build-agent-images detect job now hardcodes the Hermes commit pinned in install_hermes.sh instead of reading hermes-agent/commits/main, so the bake stays freshness-stable and base-versions.json stamps the baked commit.
