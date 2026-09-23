### Fixed
- Pin taos-neko-cdp base image by digest in Dockerfile.rk3588 and NOTES-cdp.md so the consumer build does not depend on a tag being published first
- Fix first-party tag naming test to key workflow tags by (image, tag) so a tag published for a different image cannot satisfy a pin; skip expression tags and document that the test checks the workflow YAML publish list, not the registry
