### Fixed

- Fix docker_installer port parsing to handle host:container format in install.ports
- Remove requires.ports from 27 service manifests, keeping only install.ports
- Add extra_hosts for host.docker.internal in perplexica and open-webui manifests
- Delete the dead requires.ports branch in docker_installer.py
