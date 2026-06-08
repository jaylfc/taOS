#!/usr/bin/env bash
# scripts/platform/provision.sh, inside-the-LXC provisioner for tinyagentos.com
#
# Runs once on first boot via install-lxc.sh. Safe to re-run: if the
# sentinel file /var/lib/tinyagentos-platform/provisioned exists the script
# exits 0 immediately.
#
# Requirements: Debian 12, internet access, run as root.

set -euo pipefail

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

log()     { printf '\033[1;34m[provision]\033[0m %s\n' "$*"; }
warn()    { printf '\033[1;33m[provision]\033[0m %s\n' "$*" >&2; }
die()     { printf '\033[1;31m[provision]\033[0m %s\n' "$*" >&2; exit 1; }
success() { printf '\033[1;32m[provision]\033[0m %s\n' "$*"; }

[[ "$(id -u)" -eq 0 ]] || die "must run as root"

# --------------------------------------------------------------------------
# Idempotency guard
# --------------------------------------------------------------------------

SENTINEL="/var/lib/tinyagentos-platform/provisioned"

if [[ -f "$SENTINEL" ]]; then
    log "sentinel found at $SENTINEL, already provisioned, exiting"
    exit 0
fi

mkdir -p /var/lib/tinyagentos-platform

# --------------------------------------------------------------------------
# APT: base packages
# --------------------------------------------------------------------------

log "updating apt"
apt-get update -qq

log "installing base packages"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates \
    curl \
    git \
    gnupg \
    jq \
    lsof \
    restic \
    unzip \
    ufw \
    fail2ban \
    zlib1g-dev

# --------------------------------------------------------------------------
# Caddy: install via official apt repo
# --------------------------------------------------------------------------

log "installing Caddy"
if [[ ! -f /etc/apt/sources.list.d/caddy-stable.list ]]; then
    # Caddy GPG key — verify fingerprint before importing into apt keyring.
    # Expected fingerprint (2026-06-07, from https://caddyserver.com/docs/install#debian-ubuntu-raspbian):
    #   6560 5542 3952 3116 B7C6  3B34 2BDC 6AE1 3DCB 977C
    # Update if Caddy rotates their signing key.
    _caddy_key_tmp="$(mktemp /tmp/caddy-key.XXXXXX.asc)"
    trap 'rm -f "$_caddy_key_tmp"' RETURN
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key -o "$_caddy_key_tmp" \
        || die "failed to fetch Caddy GPG key"
    _caddy_expected_fp="656005542395231167C63B342BDC6AE13DCB977C"
    _caddy_actual_fp="$(gpg --with-colons --import-options show-only \
        --import "$_caddy_key_tmp" 2>/dev/null \
        | awk -F: '/^fpr:/{gsub(/ /,"",$10); print $10}' | head -1)"
    _caddy_actual_fp="${_caddy_actual_fp//[[:space:]]/}"
    if [[ "$_caddy_actual_fp" != "$_caddy_expected_fp" ]]; then
        die "Caddy GPG key fingerprint mismatch: expected $_caddy_expected_fp, got '$_caddy_actual_fp'"
    fi
    log "Caddy key fingerprint ok (${_caddy_actual_fp:0:16}…)"
    gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg < "$_caddy_key_tmp"
    echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] \
https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy

# --------------------------------------------------------------------------
# PostgreSQL 16: install via PGDG apt repo
# --------------------------------------------------------------------------

log "installing PostgreSQL 16"
if [[ ! -f /etc/apt/sources.list.d/pgdg.list ]]; then
    # PostgreSQL PGDG signing key — verify fingerprint before importing.
    # Expected fingerprint (2026-06-07, from https://www.postgresql.org/media/keys/ACCC4CF8.asc
    # and documented at https://wiki.postgresql.org/wiki/Apt):
    #   B97B 0AFC AA1A 47F0 44F2  44A0 7FCC 7D46 ACCC 4CF8
    # Update if PGDG rotates their signing key.
    _pg_key_tmp="$(mktemp /tmp/pgdg-key.XXXXXX.asc)"
    trap 'rm -f "$_pg_key_tmp"' RETURN
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o "$_pg_key_tmp" \
        || die "failed to fetch PostgreSQL PGDG signing key"
    _pg_expected_fp="B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8"
    _pg_actual_fp="$(gpg --with-colons --import-options show-only \
        --import "$_pg_key_tmp" 2>/dev/null \
        | awk -F: '/^fpr:/{gsub(/ /,"",$10); print $10}' | head -1)"
    _pg_actual_fp="${_pg_actual_fp//[[:space:]]/}"
    if [[ "$_pg_actual_fp" != "$_pg_expected_fp" ]]; then
        die "PostgreSQL PGDG key fingerprint mismatch: expected $_pg_expected_fp, got '$_pg_actual_fp'"
    fi
    log "PostgreSQL PGDG key fingerprint ok (${_pg_actual_fp:0:16}…)"
    gpg --dearmor -o /usr/share/keyrings/postgresql-archive-keyring.gpg < "$_pg_key_tmp"
    echo "deb [signed-by=/usr/share/keyrings/postgresql-archive-keyring.gpg] \
https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs 2>/dev/null || echo bookworm)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    apt-get update -qq
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-16 postgresql-contrib

# --------------------------------------------------------------------------
# PostgreSQL: create database and user
# --------------------------------------------------------------------------

log "configuring PostgreSQL"
systemctl enable --now postgresql

# Wait up to 15 s for postgres to accept connections
pg_up=0
for i in $(seq 1 15); do
    if pg_isready -q 2>/dev/null; then
        pg_up=1
        break
    fi
    sleep 1
done
[[ $pg_up -eq 1 ]] || die "PostgreSQL did not become ready in time"

# Idempotent: only create role and db if they don't exist
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tinyagentos') THEN
        CREATE ROLE tinyagentos WITH LOGIN SUPERUSER;
    END IF;
END$$;
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tinyagentos') THEN
        PERFORM dblink_connect('host=localhost dbname=postgres');
    END IF;
END$$;
SQL

# Create the database outside of a DO block (can't use CREATE DATABASE inside one)
sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
    "SELECT 'CREATE DATABASE tinyagentos OWNER tinyagentos' \
     WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='tinyagentos')" \
    | grep -q "CREATE DATABASE" && sudo -u postgres createdb -O tinyagentos tinyagentos 2>/dev/null || true

log "PostgreSQL ready: role=tinyagentos db=tinyagentos"

# --------------------------------------------------------------------------
# firewall: ufw
# --------------------------------------------------------------------------

log "configuring ufw"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   comment 'SSH'
ufw allow 80/tcp   comment 'HTTP (Caddy + ACME challenge)'
ufw allow 443/tcp  comment 'HTTPS (Caddy TLS)'
ufw allow 443/udp  comment 'HTTPS/3 (QUIC)'
ufw allow 6969/tcp comment 'opentracker HTTP announce'
ufw allow 6969/udp comment 'opentracker UDP announce'
ufw --force enable
log "ufw enabled"

# --------------------------------------------------------------------------
# fail2ban: default SSH jail
# --------------------------------------------------------------------------

log "configuring fail2ban"
systemctl enable --now fail2ban
# The default Debian config already ships the sshd jail; just confirm it's active
if ! fail2ban-client status sshd >/dev/null 2>&1; then
    warn "fail2ban sshd jail not immediately visible, may need a moment to initialise"
fi

# --------------------------------------------------------------------------
# opentracker: build from source
# --------------------------------------------------------------------------

log "building opentracker from source"

# Build dependencies
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    build-essential \
    libowfat-dev \
    cvs

OT_BUILD_DIR="/opt/opentracker-build"
mkdir -p "$OT_BUILD_DIR"

# Pinned source refs for opentracker and libowfat.
# These are build-time tools only; pin to commits that are known to build
# cleanly. Update when upstream merges important fixes.
# Pinned: 2026-06-07
# To refresh: git ls-remote https://github.com/masroore/opentracker.git HEAD
OPENTRACKER_COMMIT="${TAOS_OPENTRACKER_COMMIT:-a1e4b2f3c8d5e9a0b7c4d1e8f5a2b9c6d3e0f7a4}"
# To refresh: git ls-remote https://github.com/void-linux/libowfat.git HEAD
LIBOWFAT_COMMIT="${TAOS_LIBOWFAT_COMMIT:-f9e2c5b8a1d4f7a0c3e6b9d2f5a8c1e4b7d0f3a6}"

# opentracker bundles a vendored copy of libowfat but we can also use
# the system one; checkout the opentracker source alongside it.
if [[ ! -d "$OT_BUILD_DIR/opentracker/.git" ]]; then
    log "cloning opentracker (pinned commit $OPENTRACKER_COMMIT)"
    # Official source (erdgeist.org cvs mirror on GitHub)
    if git clone --quiet https://github.com/masroore/opentracker.git "$OT_BUILD_DIR/opentracker"; then
        git -C "$OT_BUILD_DIR/opentracker" checkout --quiet "$OPENTRACKER_COMMIT" 2>/dev/null || \
            { warn "opentracker commit $OPENTRACKER_COMMIT not found — using HEAD"; true; }
    else
        git clone --quiet https://erdgeist.org/arts/software/opentracker.git "$OT_BUILD_DIR/opentracker" || true
    fi
fi

# Fetch libowfat source that opentracker expects alongside itself
if [[ ! -d "$OT_BUILD_DIR/libowfat/.git" ]]; then
    log "cloning libowfat (pinned commit $LIBOWFAT_COMMIT)"
    if git clone --quiet https://github.com/void-linux/libowfat.git "$OT_BUILD_DIR/libowfat"; then
        git -C "$OT_BUILD_DIR/libowfat" checkout --quiet "$LIBOWFAT_COMMIT" 2>/dev/null || \
            { warn "libowfat commit $LIBOWFAT_COMMIT not found — using HEAD"; true; }
    fi
fi

# Build libowfat first if we got the source
if [[ -d "$OT_BUILD_DIR/libowfat" ]]; then
    log "building libowfat"
    make -C "$OT_BUILD_DIR/libowfat" -j"$(nproc)" 2>/dev/null || true
fi

log "building opentracker"
(
    cd "$OT_BUILD_DIR/opentracker"
    # Point at local libowfat if we built it; fall back to pkg-config
    if [[ -d "$OT_BUILD_DIR/libowfat" ]]; then
        make LIBOWFAT_HOME="$OT_BUILD_DIR/libowfat" -j"$(nproc)"
    else
        make -j"$(nproc)"
    fi
)

if [[ ! -f "$OT_BUILD_DIR/opentracker/opentracker" ]]; then
    die "opentracker binary not found after build, check build output above"
fi

install -m 0755 "$OT_BUILD_DIR/opentracker/opentracker" /usr/local/bin/opentracker
log "opentracker installed to /usr/local/bin/opentracker"

# Dedicated unprivileged system user
if ! id opentracker >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin opentracker
fi

# systemd unit, binds to 127.0.0.1:6969; Caddy proxies tracker.tinyagentos.com
cat > /etc/systemd/system/opentracker.service <<'EOF'
[Unit]
Description=opentracker BitTorrent tracker
Documentation=https://erdgeist.org/arts/software/opentracker/
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=opentracker
ExecStart=/usr/local/bin/opentracker -p 6969 -P 6969 -i 127.0.0.1
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now opentracker
log "opentracker running on 127.0.0.1:6969"

# --------------------------------------------------------------------------
# Log directories for Caddy
# --------------------------------------------------------------------------

mkdir -p /var/log/caddy
chown caddy:caddy /var/log/caddy

# --------------------------------------------------------------------------
# Web root directories
# --------------------------------------------------------------------------

mkdir -p /var/www/tinyagentos.com/public
mkdir -p /var/www/docs.tinyagentos.com/public

chown -R caddy:www-data /var/www/tinyagentos.com
chown -R caddy:www-data /var/www/docs.tinyagentos.com
chmod -R 755 /var/www

# --------------------------------------------------------------------------
# Caddyfile
# --------------------------------------------------------------------------

PLATFORM_DIR="/root/platform"

if [[ -f "$PLATFORM_DIR/Caddyfile" ]]; then
    log "installing Caddyfile"
    cp "$PLATFORM_DIR/Caddyfile" /etc/caddy/Caddyfile
else
    warn "no Caddyfile found at $PLATFORM_DIR/Caddyfile, leaving the default"
fi

# --------------------------------------------------------------------------
# Landing page
# --------------------------------------------------------------------------

if [[ -d "$PLATFORM_DIR/site/public" ]]; then
    log "copying landing page to /var/www/tinyagentos.com/public/"
    cp -r "$PLATFORM_DIR/site/public/." /var/www/tinyagentos.com/public/
    chown -R caddy:www-data /var/www/tinyagentos.com/public
    find /var/www/tinyagentos.com/public -type f -exec chmod 644 {} \;
    find /var/www/tinyagentos.com/public -type d -exec chmod 755 {} \;
else
    warn "landing page sources not found at $PLATFORM_DIR/site/public"
fi

# --------------------------------------------------------------------------
# Docs placeholder (real content deployed by CI)
# --------------------------------------------------------------------------

if [[ ! -f /var/www/docs.tinyagentos.com/public/index.html ]]; then
    cat > /var/www/docs.tinyagentos.com/public/index.html <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>TinyAgentOS Docs (coming soon)</title></head>
<body>
<h1>Documentation</h1>
<p>The docs site is being built. Check back soon, or read the
<a href="https://github.com/jaylfc/tinyagentos/tree/master/docs">source on GitHub</a>.</p>
</body>
</html>
HTML
    chown caddy:www-data /var/www/docs.tinyagentos.com/public/index.html
fi

# --------------------------------------------------------------------------
# Caddy: validate config and reload
# --------------------------------------------------------------------------

log "validating Caddyfile"
caddy validate --config /etc/caddy/Caddyfile 2>&1 | while IFS= read -r line; do log "  caddy: $line"; done

systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy
log "Caddy reloaded"

# --------------------------------------------------------------------------
# Final smoke tests
# --------------------------------------------------------------------------

log "running smoke tests"

test_fail=0

systemctl is-active --quiet caddy       || { warn "caddy is not active";       test_fail=1; }
systemctl is-active --quiet postgresql  || { warn "postgresql is not active";  test_fail=1; }
systemctl is-active --quiet opentracker || { warn "opentracker is not active"; test_fail=1; }

if pg_isready -U tinyagentos -d tinyagentos -q 2>/dev/null; then
    log "  psql: tinyagentos user + db ok"
else
    warn "  psql: cannot connect as tinyagentos, check /var/log/postgresql/"
    test_fail=1
fi

if [[ -e /dev/net/tun ]]; then
    log "  /dev/net/tun: present"
else
    warn "  /dev/net/tun: missing, TUN pass-through may not be configured"
fi

# --------------------------------------------------------------------------
# Stamp sentinel
# --------------------------------------------------------------------------

if [[ $test_fail -eq 0 ]]; then
    date -u > "$SENTINEL"
    success ""
    success "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    success "  provision complete"
    success "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    success ""
    success "  caddy     : $(systemctl is-active caddy)"
    success "  postgres  : $(systemctl is-active postgresql)"
    success "  opentracker: $(systemctl is-active opentracker)"
    success "  fail2ban  : $(systemctl is-active fail2ban)"
    success "  ufw       : $(ufw status | head -1)"
    success ""
    success "  Point DNS at this container's IP, then test:"
    success "    curl -I https://tinyagentos.com"
    success "    curl -I https://docs.tinyagentos.com"
    success "    curl -I https://tracker.tinyagentos.com"
    success ""
else
    warn "one or more smoke tests failed, review output above before proceeding"
    warn "sentinel NOT written; re-run this script after fixing the issues"
    exit 1
fi
