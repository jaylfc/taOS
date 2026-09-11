#### RED (origin/dev, buggy code - fix absent)
---
FAILED tests/installers/test_docker_installer_ports_r2_34.py::TestQdrantHostContainerPorts::test_qdrant_manifest_does_not_raise_on_install
FAILED tests/installers/test_docker_installer_ports_r2_34.py::TestExtraHostsForHostDockerInternal::test_perplexica_manifest_adds_extra_hosts
FAILED tests/installers/test_docker_installer_ports_r2_34.py::TestExtraHostsForHostDockerInternal::test_open_webui_manifest_adds_extra_hosts
FAILED tests/installers/test_docker_installer_ports_r2_34.py::TestExtraHostsForHostDockerInternal::test_no_extra_hosts_when_host_docker_internal_not_referenced
4 failed, 81 passed in 1.33s

#### GREEN (BASE, fix applied)
---
85 passed in 1.03s

#### GREEN (audit manifests BASE)
---
3 passed in 0.28s

Docs-Reviewed: no doc change needed