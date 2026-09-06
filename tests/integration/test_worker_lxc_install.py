"""T11: Integration test — fresh worker-LXC install on a clean Ubuntu KVM VM.

Provisions a clean Ubuntu 24.04 VM, runs install-worker.sh from the
feat/worker-as-lxc branch (Phase 1 + Phase 2), then asserts:

  - taos-worker LXC is RUNNING
  - LXC has security.privileged + security.nesting
  - nftables :8443 forward is in place
  - Nested incus inside the worker LXC is reachable
  - Worker registered with the controller

Prerequisites (provisioned out-of-band before these tests):
  - A controller VM named $TAOS_CONTROLLER_VM (default: taos-controller-test)
    running install-server.sh on the KVM host.

Run:
    TAOS_INTEGRATION=1 pytest tests/integration/test_worker_lxc_install.py -v
"""
import os

import pytest

from .conftest import (
    INTEGRATION,
    _ssh,
    _ssh_vm,
)

CONTROLLER_VM = os.environ.get("TAOS_CONTROLLER_VM", "taos-controller-test")


def _controller_ip() -> str:
    """Return the IP of the assumed-running controller VM."""
    r = _ssh(
        f"sudo virsh domifaddr {CONTROLLER_VM} | "
        f"awk '/ipv4/ {{print $4}}' | cut -d/ -f1"
    )
    return r.stdout.strip()


@pytest.mark.skipif(not INTEGRATION, reason="set TAOS_INTEGRATION=1 to run")
def test_worker_lxc_install_full_flow(ubuntu_vm):
    """Phase-1 + Phase-2 install-worker.sh produces a correctly configured LXC."""
    vm_name, vm_ip = ubuntu_vm
    controller_ip = _controller_ip()
    assert controller_ip, f"controller VM {CONTROLLER_VM} is not running"

    # Run install-worker.sh from the feature branch.
    install_cmd = (
        "curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/"
        "feat/worker-as-lxc/scripts/install-worker.sh | "
        f"sudo bash -s -- http://{controller_ip}:6969"
    )
    r = _ssh_vm(vm_ip, install_cmd, timeout=900)
    assert r.returncode == 0, (
        f"install-worker.sh failed (rc={r.returncode}):\n{r.stderr[-800:]}"
    )

    # Worker LXC exists and is running.
    r = _ssh_vm(vm_ip, "sudo incus list --format=csv -c ns")
    assert "taos-worker,RUNNING" in r.stdout, (
        f"taos-worker LXC not in RUNNING state:\n{r.stdout}"
    )

    # LXC has privileged + nesting enabled.
    r = _ssh_vm(vm_ip, "sudo incus config show taos-worker")
    assert 'security.privileged: "true"' in r.stdout, (
        f"security.privileged not set:\n{r.stdout[-600:]}"
    )
    assert 'security.nesting: "true"' in r.stdout, (
        f"security.nesting not set:\n{r.stdout[-600:]}"
    )

    # nftables port-forward for :8443 is present.
    r = _ssh_vm(vm_ip, "sudo nft list ruleset")
    assert "8443" in r.stdout, (
        f"nftables :8443 forward rule is missing:\n{r.stdout[-600:]}"
    )

    # Nested incus inside the worker LXC is reachable.
    r = _ssh_vm(
        vm_ip,
        "sudo incus exec taos-worker -- incus list --format=csv -c ns",
        timeout=60,
    )
    assert r.returncode == 0, (
        f"Nested incus inside taos-worker is not reachable:\n{r.stderr}"
    )

    # Worker registered with the controller.
    r = _ssh(f"curl -sf http://{controller_ip}:6969/api/cluster/workers")
    assert r.returncode == 0, (
        f"Could not reach controller API:\n{r.stderr}"
    )
    assert ("taos-worker" in r.stdout or vm_name in r.stdout), (
        f"Worker not registered with controller:\n{r.stdout[:600]}"
    )
