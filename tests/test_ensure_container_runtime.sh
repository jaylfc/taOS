#!/usr/bin/env bash
# Simple bats-style tests for ensure_container_runtime
# Tests that the key requirements are met

set -euo pipefail

SCRIPT="scripts/install-server.sh"

echo "test: apk branch defines _incus_pkg with incus and incus-client"
if grep -A10 "elif command -v apk >/dev/null 2>&1; then" "$SCRIPT" | grep -q "local _incus_pkg=\"incus incus-client\""; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: apk branch conditionally adds incus-openrc when OpenRC is present"
if grep -A15 "elif command -v apk >/dev/null 2>&1; then" "$SCRIPT" | grep -q "_incus_pkg=\"\$_incus_pkg incus-openrc\""; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: apk branch probes apk search before attempting install"
if grep -A15 "elif command -v apk >/dev/null 2>&1; then" "$SCRIPT" | grep -q "apk search -x incus"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: apk branch sets installed=1 on success"
if grep -A15 "elif command -v apk >/dev/null 2>&1; then" "$SCRIPT" | grep -q "installed=1"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: pacman branch probes availability with pacman -Si"
if grep -A5 "elif command -v pacman >/dev/null 2>&1; then" "$SCRIPT" | grep -q "pacman -Si incus"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: pacman branch sets installed=1 on success"
if grep -A15 "elif command -v pacman >/dev/null 2>&1; then" "$SCRIPT" | grep -q "installed=1"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: Alpine+systemd warning case is present"
if grep -q "incus package installed but no incus.service unit for systemd" "$SCRIPT"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: init system detection uses _incusd_started variable"
if grep -q "local _incusd_started=0" "$SCRIPT"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: comment updated to include Arch/Alpine in auto-invoke section"
if grep -q "On Linux, if no runtime is found, we install Incus via the system" "$SCRIPT" && \
   grep -q "package manager on Debian/Ubuntu/Fedora/Arch/Alpine" "$SCRIPT"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: hint text preserved for Arch"
if grep -q "container runtime: Arch detected — install Incus manually with:" "$SCRIPT"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "test: hint text preserved for Alpine"
if grep -q "container runtime: Alpine detected — install Incus manually with:" "$SCRIPT"; then
    echo "PASS"
else
    echo "FAIL"
    exit 1
fi

echo "All tests passed!"
