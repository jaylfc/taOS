### Fixed
- Fixed docker_installer to handle host:container port format in manifests (e.g., "6333:6333")
- Removed requires.ports from all 27 service manifests, keeping only install.ports
- Added extra_hosts: host.docker.internal:host-gateway when manifests reference host.docker.internal
- Fixed dead branch in docker_installer._generate_compose that caused ValueError when parsing host:container format
