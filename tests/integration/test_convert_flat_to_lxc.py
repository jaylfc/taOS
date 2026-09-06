"""T12: Integration test — convert a flat-mode worker install to worker-LXC mode.

Sequence:
  1. Provision fresh Ubuntu 24.04 VM.
  2. Install the LEGACY flat-mode worker from the last pre-T5 commit (1e17272).
  3. Deploy a stub agent through the controller's API; verify it lands on the
     host's flat-mode incus (not inside a nested LXC).
  4. Hash the agent's memory dir on shared cluster storage via the controller.
  5. Run ``taos-worker-ctl convert-to-lxc http://<controller>:6969 -y``.
  6. Assert:
       - taos-worker LXC now exists and is RUNNING.
       - The stub agent runs INSIDE nested incus (not on the host).
       - The flat-mode taos-agent-<name> container is gone from the host.
       - Memory dir hash on shared storage is unchanged (data survived).
  7. Cleanup via fixture teardown.

Run:
    TAOS_INTEGRATION=1 pytest tests/integration/test_convert_flat_to_lxc.py -v
"""
import json
import os
import time

import pytest

from .conftest import (
    INTEGRATION,
    _ssh,
    _ssh_vm,
)

CONTROLLER_VM = os.environ.get("TAOS_CONTROLLER_VM", "taos-controller-test")
# Last commit SHA before T5 (LXC nesting) landed — the legacy flat-mode installer.
LEGACY_COMMIT = os.environ.get("TAOS_LEGACY_COMMIT", "1e17272")
STUB_AGENT_NAME = "integ-stub-agent"


def _controller_ip() -> str:
    """Return the IP of the assumed-running controller VM."""
    r = _ssh(
        f"sudo virsh domifaddr {CONTROLLER_VM} | "
        f"awk '/ipv4/ {{print $4}}' | cut -d/ -f1"
    )
    return r.stdout.strip()


def _deploy_stub_agent(controller_ip: str, vm_ip: str) -> None:
    """Ask the controller to deploy a stub agent onto the worker VM."""
    payload = json.dumps({
        "name": STUB_AGENT_NAME,
        "worker_host": vm_ip,
        "image": "ubuntu:24.04",
        "memory_limit": "256MiB",
        "cpu_limit": 1,
    })
    r = _ssh(
        f"curl -sf -X POST -H 'Content-Type: application/json' "
        f"-d {payload!r} http://{controller_ip}:6969/api/cluster/agents"
    )
    assert r.returncode == 0, (
        f"Could not reach controller agent deploy API:\n{r.stderr}"
    )


def _agent_container_name() -> str:
    return f"taos-agent-{STUB_AGENT_NAME}"


def _memory_dir_hash(controller_ip: str) -> str:
    """Return sha256sum of the agent's memory dir on shared cluster storage."""
    r = _ssh(
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-i ~/taos-install-test/vm_key "
        f"ubuntu@{controller_ip} "
        f"'find /srv/taos/shared/{_agent_container_name()} -type f | "
        f"sort | xargs sha256sum 2>/dev/null | sha256sum'"
    )
    return r.stdout.strip()


@pytest.mark.skipif(not INTEGRATION, reason="set TAOS_INTEGRATION=1 to run")
def test_convert_flat_to_lxc(ubuntu_vm):
    """Full flat→LXC conversion preserves agent data and migrates containers."""
    vm_name, vm_ip = ubuntu_vm
    controller_ip = _controller_ip()
    assert controller_ip, f"controller VM {CONTROLLER_VM} is not running"

    # --- Step 1: Install the legacy flat-mode worker ---
    legacy_install_cmd = (
        f"curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/"
        f"{LEGACY_COMMIT}/scripts/install-worker.sh | "
        f"sudo bash -s -- http://{controller_ip}:6969"
    )
    r = _ssh_vm(vm_ip, legacy_install_cmd, timeout=900)
    assert r.returncode == 0, (
        f"Legacy install-worker.sh failed (rc={r.returncode}):\n{r.stderr[-800:]}"
    )

    # Confirm flat-mode: no taos-worker LXC at this point.
    r = _ssh_vm(vm_ip, "sudo incus list --format=csv -c ns 2>/dev/null")
    assert "taos-worker" not in r.stdout, (
        f"Unexpected taos-worker LXC present after legacy install:\n{r.stdout}"
    )

    # --- Step 2: Deploy a stub agent in flat mode ---
    _deploy_stub_agent(controller_ip, vm_ip)

    # Wait for the agent container to appear on the host.
    deadline = time.time() + 60
    agent_running = False
    while time.time() < deadline:
        r = _ssh_vm(vm_ip, "sudo incus list --format=csv -c ns 2>/dev/null")
        if _agent_container_name() in r.stdout and "RUNNING" in r.stdout:
            agent_running = True
            break
        time.sleep(3)
    assert agent_running, (
        f"Stub agent {_agent_container_name()} never reached RUNNING state "
        f"in flat-mode host incus"
    )

    # Confirm agent is on the HOST incus (flat mode), not nested.
    r = _ssh_vm(
        vm_ip,
        "sudo incus exec taos-worker -- incus list --format=csv -c ns 2>/dev/null",
    )
    # taos-worker doesn't exist yet, so this should fail or return empty.
    assert _agent_container_name() not in r.stdout, (
        "Agent appears inside nested LXC before conversion — unexpected"
    )

    # --- Step 3: Hash the memory dir before conversion ---
    pre_hash = _memory_dir_hash(controller_ip)

    # --- Step 4: Run convert-to-lxc ---
    convert_cmd = (
        f"cd ~/tinyagentos && "
        f"taos-worker-ctl worker convert-to-lxc "
        f"http://{controller_ip}:6969 -y"
    )
    r = _ssh_vm(vm_ip, convert_cmd, timeout=900)
    assert r.returncode == 0, (
        f"taos-worker-ctl convert-to-lxc failed (rc={r.returncode}):\n"
        f"stdout={r.stdout[-600:]}\nstderr={r.stderr[-400:]}"
    )

    # --- Step 5: taos-worker LXC now exists and is RUNNING ---
    r = _ssh_vm(vm_ip, "sudo incus list --format=csv -c ns")
    assert "taos-worker,RUNNING" in r.stdout, (
        f"taos-worker LXC not RUNNING after conversion:\n{r.stdout}"
    )

    # --- Step 6: Agent runs INSIDE nested incus ---
    deadline = time.time() + 60
    nested_running = False
    while time.time() < deadline:
        r = _ssh_vm(
            vm_ip,
            "sudo incus exec taos-worker -- incus list --format=csv -c ns",
            timeout=30,
        )
        if _agent_container_name() in r.stdout and "RUNNING" in r.stdout:
            nested_running = True
            break
        time.sleep(3)
    assert nested_running, (
        f"Agent {_agent_container_name()} not RUNNING inside nested incus "
        f"after conversion"
    )

    # --- Step 7: Flat-mode agent gone from host incus ---
    r = _ssh_vm(vm_ip, "sudo incus list --format=csv -c ns")
    host_containers = [
        line.split(",")[0]
        for line in r.stdout.splitlines()
        if line.strip()
    ]
    # Only taos-worker itself should remain at the host level.
    assert _agent_container_name() not in host_containers, (
        f"Flat-mode agent {_agent_container_name()} still present on host "
        f"incus after conversion:\n{r.stdout}"
    )

    # --- Step 8: Memory dir hash unchanged ---
    post_hash = _memory_dir_hash(controller_ip)
    assert pre_hash == post_hash, (
        f"Agent memory dir hash changed during conversion "
        f"(before={pre_hash!r}, after={post_hash!r}) — data loss detected"
    )
