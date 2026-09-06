#!/usr/bin/env bash
# scripts/install-platform-lxc.sh, Proxmox LXC provisioner for tinyagentos.com
#
# Creates a Debian 12 unprivileged LXC container with the configuration
# required to host tinyagentos.com services, then runs provision.sh inside
# the new container to bring everything up.
#
# Run this on the Proxmox host as root (or with sudo).
#
# Usage:
#     sudo bash scripts/install-platform-lxc.sh
#
# Environment overrides:
#     CTID            container ID (default: next free ID from pvesh)
#     CT_IP           static IP in CIDR notation, e.g. 192.168.1.50/24
#                     (default: dhcp, Proxmox assigns from your DHCP server)
#     CT_GW           gateway IP (required when CT_IP is set; ignored for dhcp)
#     CT_BRIDGE       Proxmox bridge to attach (default: vmbr0)
#     CT_STORAGE      storage pool for rootfs (default: local-lvm)
#     CT_TEMPLATE     full path to the Debian 12 template (default: see below)
#     CT_DISK_DATA    size in GB for the separate data disk at /var/mail
#                     (default: 0, skip the data disk; set to e.g. 500 to add one)
#     PROVISION_SCRIPT path to provision.sh inside the Proxmox host filesystem
#                     (default: same directory as this script)
#
# Template default:
#     local:vztmpl/debian-12-standard_12.*_amd64.tar.zst
#     If not found: pveam update && pveam download local debian-12-standard

set -euo pipefail

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

log()     { printf '\033[1;34m[install-lxc]\033[0m %s\n' "$*"; }
warn()    { printf '\033[1;33m[install-lxc]\033[0m %s\n' "$*" >&2; }
die()     { printf '\033[1;31m[install-lxc]\033[0m %s\n' "$*" >&2; exit 1; }
success() { printf '\033[1;32m[install-lxc]\033[0m %s\n' "$*"; }

# --------------------------------------------------------------------------
# Prereq checks
# --------------------------------------------------------------------------

[[ "$(id -u)" -eq 0 ]] || die "must run as root on the Proxmox host"

for cmd in pct pvesh pveam; do
    command -v "$cmd" >/dev/null 2>&1 || die "$cmd not found, this script must run on a Proxmox host"
done

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROVISION_SCRIPT="${PROVISION_SCRIPT:-$SCRIPT_DIR/provision.sh}"

[[ -f "$PROVISION_SCRIPT" ]] || die "provision.sh not found at $PROVISION_SCRIPT"

# Next free CTID: pvesh returns the next available integer
if [[ -z "${CTID:-}" ]]; then
    CTID="$(pvesh get /cluster/nextid 2>/dev/null | tr -d '"')"
    [[ -n "$CTID" ]] || die "could not determine next free container ID; set CTID manually"
fi
log "CTID=$CTID"

CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_STORAGE="${CT_STORAGE:-local-lvm}"
CT_IP="${CT_IP:-dhcp}"
CT_GW="${CT_GW:-}"
CT_DISK_DATA="${CT_DISK_DATA:-0}"

# Resolve template
if [[ -n "${CT_TEMPLATE:-}" ]]; then
    tmpl="$CT_TEMPLATE"
else
    tmpl="$(find /var/lib/vz/template/vztmpl/ -name 'debian-12-standard_12*_amd64.tar.zst' 2>/dev/null | sort -V | tail -1)"
fi

if [[ -z "$tmpl" ]]; then
    die "Debian 12 standard template not found.
  Download it with:
    pveam update
    pveam download local debian-12-standard
  Then re-run this script."
fi

# Normalise to a Proxmox storage path if given as a filesystem path
if [[ "$tmpl" == /var/lib/vz/template/vztmpl/* ]]; then
    tmpl_name="$(basename "$tmpl")"
    tmpl="local:vztmpl/$tmpl_name"
fi
log "template=$tmpl"

# Network string for pct create
if [[ "$CT_IP" == "dhcp" ]]; then
    net_arg="name=eth0,bridge=${CT_BRIDGE},ip=dhcp"
    if [[ -z "$CT_GW" ]]; then
        true  # dhcp provides the gateway
    fi
else
    [[ -n "$CT_GW" ]] || die "CT_GW must be set when CT_IP is a static address"
    net_arg="name=eth0,bridge=${CT_BRIDGE},ip=${CT_IP},gw=${CT_GW}"
fi

# --------------------------------------------------------------------------
# Create the container
# --------------------------------------------------------------------------

log "creating container $CTID (hostname=tinyagentos-platform)"

pct create "$CTID" "$tmpl" \
    --hostname    tinyagentos-platform \
    --cores       4 \
    --memory      8192 \
    --swap        1024 \
    --rootfs      "${CT_STORAGE}:100" \
    --net0        "$net_arg" \
    --ostype      debian \
    --unprivileged 1 \
    --features    nesting=1,keyctl=1 \
    --start       0 \
    --onboot      1

log "container $CTID created"

# --------------------------------------------------------------------------
# Add raw LXC config entries for TUN device pass-through
# --------------------------------------------------------------------------

CT_CONF="/etc/pve/lxc/${CTID}.conf"
[[ -f "$CT_CONF" ]] || die "container config not found at $CT_CONF"

log "adding TUN device entries to $CT_CONF"
cat >> "$CT_CONF" <<'EOF'

# TUN device pass-through, required for Headscale / WireGuard mesh
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF

# --------------------------------------------------------------------------
# Optional data disk for /var/mail
# --------------------------------------------------------------------------

if [[ "${CT_DISK_DATA}" -gt 0 ]]; then
    log "adding ${CT_DISK_DATA} GB data disk (mp0 -> /var/mail)"
    pct set "$CTID" --mp0 "${CT_STORAGE}:${CT_DISK_DATA},mp=/var/mail"
fi

# --------------------------------------------------------------------------
# Start the container and wait for network
# --------------------------------------------------------------------------

log "starting container $CTID"
pct start "$CTID"

log "waiting for network inside the container (up to 60 s)..."
net_up=0
for i in $(seq 1 60); do
    if pct exec "$CTID" -- bash -c "ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 || curl -sf --max-time 3 https://deb.debian.org/ >/dev/null 2>&1"; then
        net_up=1
        break
    fi
    sleep 1
done

if [[ $net_up -eq 0 ]]; then
    warn "network not confirmed after 60 s, check the container's IP and routing"
    warn "  pct exec $CTID -- ip a"
    warn "  pct exec $CTID -- ip r"
    die "aborting: provision.sh needs internet access to install packages"
fi
log "network is up inside container $CTID"

# --------------------------------------------------------------------------
# Copy and run provision.sh
# --------------------------------------------------------------------------

log "copying provision.sh into container"
pct push "$CTID" "$PROVISION_SCRIPT" /root/provision.sh
pct exec "$CTID" -- chmod +x /root/provision.sh

# Copy the Caddyfile and site sources so provision.sh can place them
CADDYFILE="${SCRIPT_DIR}/Caddyfile"
if [[ -f "$CADDYFILE" ]]; then
    log "copying Caddyfile"
    pct exec "$CTID" -- mkdir -p /root/platform
    pct push "$CTID" "$CADDYFILE" /root/scripts/platform/Caddyfile
fi

SITE_DIR="${SCRIPT_DIR}/site/public"
if [[ -d "$SITE_DIR" ]]; then
    log "copying site/public into container"
    pct exec "$CTID" -- mkdir -p /root/site/public
    # pct push doesn't support directories; use tar
    tar -C "$SCRIPT_DIR" -czf /tmp/taos-site-public.tar.gz site/public
    pct push "$CTID" /tmp/taos-site-public.tar.gz /root/taos-site-public.tar.gz
    pct exec "$CTID" -- bash -c "cd /root/platform && tar -xzf /root/taos-site-public.tar.gz"
    rm -f /tmp/taos-site-public.tar.gz
fi

log "running provision.sh inside container $CTID"
pct exec "$CTID" -- bash /root/provision.sh

# --------------------------------------------------------------------------
# Get the container IP for the summary
# --------------------------------------------------------------------------

ct_ip="$(pct exec "$CTID" -- bash -c "ip -4 -o addr show eth0 2>/dev/null | awk '{print \$4}' | cut -d/ -f1" 2>/dev/null || true)"
[[ -z "$ct_ip" ]] && ct_ip="<check with: pct exec $CTID -- ip a>"

# --------------------------------------------------------------------------
# Final summary
# --------------------------------------------------------------------------

success ""
success "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "  platform LXC provisioning complete"
success "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success ""
success "  Container ID : $CTID"
success "  IP address   : $ct_ip"
success "  Hostname     : tinyagentos-platform"
success ""
success "  SSH in:"
success "    ssh root@$ct_ip"
success ""
success "  Next steps:"
success "    1. Point DNS records for all four subdomains at $ct_ip"
success "       (see scripts/platform/Caddyfile for the full DNS record list)"
success "    2. Once DNS propagates, Caddy will auto-issue Let's Encrypt certs"
success "       via HTTP-01 on first request to each domain."
success "    3. Verify with: curl -I https://tinyagentos.com"
success ""
success "  Smoke tests:"
success "    pct exec $CTID -- bash -c 'systemctl is-active caddy postgresql opentracker'"
success "    pct exec $CTID -- bash -c 'caddy validate --config /etc/caddy/Caddyfile'"
success "    pct exec $CTID -- bash -c 'psql -U tinyagentos -c \"\\\\conninfo\"'"
success "    pct exec $CTID -- bash -c 'test -c /dev/net/tun && echo tun-ok'"
success ""
success "  See docs/deploy/platform.md for the full runbook."
success ""
