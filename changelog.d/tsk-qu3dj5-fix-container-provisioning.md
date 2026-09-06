### Fixed
- Use `container name` not `container id` in provisioning P2 release note (changelog.d/tsk-5drlbj-agent-container-provisioning-p2.md)
- Harden provisioning inputs: treat optional config as absent when `container_provisioning` attribute is missing; bound canonical-ID component so container name stays within 63-char limit
- Destroy underlying incus container when `set_env` fails during provisioning, preventing leaked containers on terminal failed requests