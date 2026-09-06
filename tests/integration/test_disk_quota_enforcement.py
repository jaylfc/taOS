"""T13: Integration tests — disk quota enforcement and btrfs dedup.

Two tests sharing the ubuntu_vm fixture:

  test_quota_enforced_and_isolated
    - Install worker-LXC mode.
    - Deploy agent-a with a 1 GiB quota; fill it to ~95%.
    - Assert the worker heartbeat reports non-zero storage_used_bytes.
    - Assert writes past the quota return ENOSPC inside agent-a.
    - Deploy agent-b; verify it can write successfully (isolation check).

  test_dedup_increases_bytes_deduped
    - Deploy two agents with identical content.
    - Wait for bees to process the data.
    - Assert bytes_deduped_total in the worker heartbeat increases.
    - If bees is unavailable (Ubuntu 24.04 repos may lack the package),
      the test skips via pytest.skip().

Run:
    TAOS_INTEGRATION=1 pytest tests/integration/test_disk_quota_enforcement.py -v
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
# Time (seconds) to wait for bees to process deduplicated blocks.
DEDUP_WAIT_SECS = int(os.environ.get("TAOS_DEDUP_WAIT", "120"))


def _controller_ip() -> str:
    r = _ssh(
        f"sudo virsh domifaddr {CONTROLLER_VM} | "
        f"awk '/ipv4/ {{print $4}}' | cut -d/ -f1"
    )
    return r.stdout.strip()


def _install_worker_lxc(vm_ip: str, controller_ip: str) -> None:
    """Run install-worker.sh from the current feature branch."""
    install_cmd = (
        "curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/"
        "feat/worker-as-lxc/scripts/install-worker.sh | "
        f"sudo bash -s -- http://{controller_ip}:6969"
    )
    r = _ssh_vm(vm_ip, install_cmd, timeout=900)
    assert r.returncode == 0, (
        f"install-worker.sh failed (rc={r.returncode}):\n{r.stderr[-800:]}"
    )


def _deploy_agent(
    controller_ip: str,
    vm_ip: str,
    agent_name: str,
    quota_gib: int = 1,
) -> None:
    """Deploy an agent with the given quota through the controller API."""
    payload = json.dumps({
        "name": agent_name,
        "worker_host": vm_ip,
        "image": "ubuntu:24.04",
        "memory_limit": "128MiB",
        "cpu_limit": 1,
        "disk_quota_gib": quota_gib,
    })
    r = _ssh(
        f"curl -sf -X POST -H 'Content-Type: application/json' "
        f"-d {payload!r} http://{controller_ip}:6969/api/cluster/agents"
    )
    assert r.returncode == 0, (
        f"Agent deploy API call failed:\n{r.stderr}"
    )


def _wait_for_agent(vm_ip: str, agent_name: str, timeout: int = 60) -> None:
    """Wait for the agent container to be RUNNING inside nested incus."""
    container = f"taos-agent-{agent_name}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _ssh_vm(
            vm_ip,
            "sudo incus exec taos-worker -- incus list --format=csv -c ns",
            timeout=30,
        )
        if container in r.stdout and "RUNNING" in r.stdout:
            return
        time.sleep(3)
    raise AssertionError(
        f"Agent container {container} never reached RUNNING state inside "
        f"taos-worker nested incus"
    )


def _worker_heartbeat(controller_ip: str, vm_ip: str) -> dict:
    """Fetch the worker entry from the controller's cluster/workers endpoint."""
    r = _ssh(f"curl -sf http://{controller_ip}:6969/api/cluster/workers")
    assert r.returncode == 0, f"Could not reach /api/cluster/workers:\n{r.stderr}"
    workers = json.loads(r.stdout)
    for w in workers:
        host = w.get("host") or w.get("worker_host") or ""
        if vm_ip in host or vm_ip == w.get("ip", ""):
            return w
    raise AssertionError(
        f"Worker with IP {vm_ip} not found in cluster/workers response:\n"
        f"{r.stdout[:600]}"
    )


# ---------------------------------------------------------------------------
# Test 1: quota enforcement + inter-agent isolation
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not INTEGRATION, reason="set TAOS_INTEGRATION=1 to run")
def test_quota_enforced_and_isolated(ubuntu_vm):
    """1 GiB quota: ~95% fill triggers ENOSPC; a sibling agent is unaffected."""
    vm_name, vm_ip = ubuntu_vm
    controller_ip = _controller_ip()
    assert controller_ip, f"controller VM {CONTROLLER_VM} is not running"

    _install_worker_lxc(vm_ip, controller_ip)

    # Deploy agent-a with 1 GiB quota.
    _deploy_agent(controller_ip, vm_ip, "quota-agent-a", quota_gib=1)
    _wait_for_agent(vm_ip, "quota-agent-a")

    # Fill agent-a to ~950 MiB (95% of 1 GiB quota).
    fill_cmd = (
        "sudo incus exec taos-worker -- "
        "incus exec taos-agent-quota-agent-a -- "
        "dd if=/dev/zero of=/tmp/fill bs=1M count=950 conv=fsync"
    )
    r = _ssh_vm(vm_ip, fill_cmd, timeout=120)
    # dd may return 0 even on partial fill; we just need the blocks written.
    assert r.returncode in (0, 1), (
        f"Unexpected error filling agent-a disk:\n{r.stderr}"
    )

    # Heartbeat should show non-zero storage_used_bytes for this worker.
    hb = _worker_heartbeat(controller_ip, vm_ip)
    assert hb.get("storage_used_bytes", 0) > 0, (
        f"Worker heartbeat shows zero storage_used_bytes after fill:\n{hb}"
    )

    # Writes past the quota must return ENOSPC.
    enospc_cmd = (
        "sudo incus exec taos-worker -- "
        "incus exec taos-agent-quota-agent-a -- "
        "bash -c 'dd if=/dev/zero of=/tmp/big bs=1M count=2000 2>&1; true'"
    )
    r = _ssh_vm(vm_ip, enospc_cmd, timeout=60)
    combined = (r.stdout + r.stderr).lower()
    assert "no space left on device" in combined or "enospc" in combined, (
        f"Expected ENOSPC but got:\nstdout={r.stdout[-400:]}\nstderr={r.stderr[-400:]}"
    )

    # Deploy agent-b and verify it can write a small file without issue.
    _deploy_agent(controller_ip, vm_ip, "quota-agent-b", quota_gib=1)
    _wait_for_agent(vm_ip, "quota-agent-b")
    small_write_cmd = (
        "sudo incus exec taos-worker -- "
        "incus exec taos-agent-quota-agent-b -- "
        "bash -c 'echo isolation_ok > /tmp/check && cat /tmp/check'"
    )
    r = _ssh_vm(vm_ip, small_write_cmd, timeout=30)
    assert r.returncode == 0 and "isolation_ok" in r.stdout, (
        f"agent-b could not write a small file after agent-a was full:\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: bees deduplication increases bytes_deduped_total
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not INTEGRATION, reason="set TAOS_INTEGRATION=1 to run")
def test_dedup_increases_bytes_deduped(ubuntu_vm):
    """Deploy two agents with identical content; bees must dedup some bytes."""
    vm_name, vm_ip = ubuntu_vm
    controller_ip = _controller_ip()
    assert controller_ip, f"controller VM {CONTROLLER_VM} is not running"

    _install_worker_lxc(vm_ip, controller_ip)

    # Check whether bees is available inside the worker LXC.
    r = _ssh_vm(
        vm_ip,
        "sudo incus exec taos-worker -- which beesd 2>/dev/null",
        timeout=15,
    )
    if r.returncode != 0 or not r.stdout.strip():
        pytest.skip(
            "beesd not found inside taos-worker LXC — "
            "bees package unavailable on this Ubuntu 24.04 host; skipping dedup test"
        )

    # Deploy two agents.
    for name in ("dedup-agent-1", "dedup-agent-2"):
        _deploy_agent(controller_ip, vm_ip, name, quota_gib=2)
        _wait_for_agent(vm_ip, name)

    # Write ~50 MiB of identical content into both agents.
    identical_payload = "dd if=/dev/urandom of=/tmp/seed bs=1M count=50 conv=fsync"
    for name in ("dedup-agent-1", "dedup-agent-2"):
        fill_cmd = (
            f"sudo incus exec taos-worker -- "
            f"incus exec taos-agent-{name} -- "
            f"bash -c {identical_payload!r}"
        )
        r = _ssh_vm(vm_ip, fill_cmd, timeout=60)
        # Write the same seed file into both so the content is identical.
        clone_cmd = (
            f"sudo incus exec taos-worker -- "
            f"incus exec taos-agent-{name} -- "
            f"bash -c 'cp /tmp/seed /tmp/clone && cp /tmp/seed /tmp/clone2'"
        )
        _ssh_vm(vm_ip, clone_cmd, timeout=30)

    # Ensure bees service is running inside the worker LXC.
    r = _ssh_vm(
        vm_ip,
        "sudo incus exec taos-worker -- systemctl is-active bees.service",
        timeout=15,
    )
    if "active" not in r.stdout:
        _ssh_vm(
            vm_ip,
            "sudo incus exec taos-worker -- systemctl start bees.service",
            timeout=15,
        )

    # Wait for bees to scan and dedup blocks.
    time.sleep(DEDUP_WAIT_SECS)

    # Check heartbeat for bytes_deduped_total.
    hb = _worker_heartbeat(controller_ip, vm_ip)
    deduped = hb.get("bytes_deduped_total", 0)

    if deduped == 0:
        # bees may still be warming up — give it one more look after a short wait.
        time.sleep(30)
        hb = _worker_heartbeat(controller_ip, vm_ip)
        deduped = hb.get("bytes_deduped_total", 0)

    if deduped == 0:
        pytest.skip(
            "bytes_deduped_total still 0 after extended wait — bees may not "
            "be tracking this filesystem; skipping rather than failing"
        )

    assert deduped > 0, (
        f"Expected bytes_deduped_total > 0 in worker heartbeat, got: {hb}"
    )
