"""Shared helpers for KVM integration tests.

Tests run against a remote KVM host where fresh Ubuntu 24.04 VMs can be
provisioned via libvirt + cloud-init. SSH credentials and host IP are
taken from required env vars — no defaults are baked in (the values are
environment-specific and must not be committed to the repo).

Required when TAOS_INTEGRATION=1:
  TAOS_KVM_HOST   IP or hostname of the KVM host
  TAOS_KVM_USER   SSH user with passwordless sudo on the KVM host
  TAOS_KVM_PASS   SSH password for that user

All tests in this directory are gated by TAOS_INTEGRATION=1; if it's not
set the tests skip without reading any of the credentials env vars.
"""
import os
import shlex
import subprocess
import time
from typing import Optional

import pytest

INTEGRATION = os.environ.get("TAOS_INTEGRATION") == "1"


def _require_env(name: str) -> str:
    """Read an env var that must be set when running integration tests."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is required when TAOS_INTEGRATION=1 (no default; "
            f"environment-specific value must come from your shell or .env)"
        )
    return value


FEDORA_HOST = _require_env("TAOS_KVM_HOST") if INTEGRATION else ""
FEDORA_USER = _require_env("TAOS_KVM_USER") if INTEGRATION else ""
FEDORA_PASS = _require_env("TAOS_KVM_PASS") if INTEGRATION else ""


def _ssh(cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a command on the Fedora KVM host via sshpass+ssh."""
    return subprocess.run(
        [
            "sshpass", "-p", FEDORA_PASS, "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{FEDORA_USER}@{FEDORA_HOST}",
            cmd,
        ],
        capture_output=True, text=True, timeout=timeout,
    )


def _provision_ubuntu_vm(
    vm_name: str,
    disk_gb: int = 12,
    vcpus: int = 4,
    mem_mb: int = 4096,
) -> None:
    """Provision a fresh Ubuntu 24.04 VM with cloud-init seed."""
    _ssh(
        f"sudo virsh destroy {vm_name} 2>/dev/null; "
        f"sudo virsh undefine {vm_name} --remove-all-storage 2>/dev/null; true"
    )
    _ssh(
        f"cd ~/taos-install-test && "
        f"cp ubuntu-24.04.qcow2 {vm_name}.qcow2 && "
        f"qemu-img resize {vm_name}.qcow2 {disk_gb}G && "
        f"sudo cp {vm_name}.qcow2 /var/lib/libvirt/images/ && "
        f"sudo virt-install --name {vm_name} --memory {mem_mb} --vcpus {vcpus} "
        f"--disk path=/var/lib/libvirt/images/{vm_name}.qcow2,format=qcow2 "
        f"--disk path=/var/lib/libvirt/images/seed-ubuntu24.iso,device=cdrom "
        f"--os-variant ubuntu24.04 --network network=default,model=virtio "
        f"--graphics none --noautoconsole --import",
        timeout=180,
    )


def _wait_for_vm_ip(vm_name: str, timeout: int = 120) -> Optional[str]:
    """Poll virsh until the VM has an IPv4 address."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _ssh(
            f"sudo virsh domifaddr {vm_name} 2>/dev/null | "
            f"awk '/ipv4/ {{print $4}}' | cut -d/ -f1"
        )
        ip = r.stdout.strip()
        if ip:
            return ip
        time.sleep(2)
    return None


def _wait_for_ssh(vm_ip: str, timeout: int = 60) -> bool:
    """Wait until SSH on the VM is responsive."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _ssh(
            f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            f"-i ~/taos-install-test/vm_key ubuntu@{vm_ip} 'echo READY' 2>&1"
        )
        if "READY" in r.stdout:
            return True
        time.sleep(3)
    return False


def _ssh_vm(vm_ip: str, cmd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a command on the inner Ubuntu VM via the Fedora host as a jump."""
    return _ssh(
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-i ~/taos-install-test/vm_key ubuntu@{vm_ip} {shlex.quote(cmd)}",
        timeout=timeout,
    )


def _destroy_vm(vm_name: str) -> None:
    """Force-destroy + undefine a VM. Idempotent."""
    _ssh(
        f"sudo virsh destroy {vm_name} 2>/dev/null; "
        f"sudo virsh undefine {vm_name} --remove-all-storage 2>/dev/null; true"
    )


@pytest.fixture
def ubuntu_vm():
    """Pytest fixture: provisions a fresh Ubuntu 24.04 VM, yields its IP,
    destroys on teardown. Test receives ``(vm_name, vm_ip)``."""
    vm_name = f"taos-int-{os.urandom(4).hex()}"
    _provision_ubuntu_vm(vm_name)
    vm_ip = _wait_for_vm_ip(vm_name)
    assert vm_ip, f"VM {vm_name} never got an IP"
    assert _wait_for_ssh(vm_ip), f"SSH on {vm_ip} not responsive"
    try:
        yield vm_name, vm_ip
    finally:
        _destroy_vm(vm_name)
