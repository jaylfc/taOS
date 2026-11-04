### Fixed
- Fixed musl-arm64 variant detection test to evaluate the real detect_variant function instead of a redefined copy
- Fixed summary output to only show LLAMACPP_MUSL_TAG for musl-arm64 variant (not all variants)
- Hardened build_from_source_musl to run git rev-parse and chmod as target user via run_as_user