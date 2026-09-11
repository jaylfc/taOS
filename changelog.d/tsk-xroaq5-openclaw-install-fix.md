### Fixed
- Fixed openclaw install script location in app-catalog/agents/openclaw/manifest.yaml (was pointing to app-catalog/agents/openclaw/scripts/install.sh, moved to scripts/install-openclaw.sh)
- Added get_manifest method to AppRegistry in tinyagentos/registry.py for proper manifest resolution
- Added 'hailo' to VALID_TARGETS in scripts/audit-manifests.py to validate hailo backend targets
