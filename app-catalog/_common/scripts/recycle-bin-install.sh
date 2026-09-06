#!/usr/bin/env bash
# Installs a system-wide soft-delete recycle bin inside an agent container.
# Called from each agent framework's install.sh. Idempotent: safe to re-run.
#
# Layer 1 of taOS's three-layer recycle-bin design (see
# docs/design/architecture-pivot-v2.md §6). Layer 3 (FS snapshots) is
# configured on the HOST, not here.
#
# Installs:
#   - trash-cli (freedesktop.org-compliant trash utilities)
#   - /usr/local/bin/rm : shadow wrapper that redirects to trash-put
#   - /var/recycle-bin/ : the recycle-bin root (trash-cli XDG config points here)
#   - tinyagentos-recycle-sweep.service + .timer : daily 30-day retention sweep
#
# Escape hatches:
#   - /usr/bin/rm    : unchanged; invoke explicitly for permanent delete
#   - TAOS_TRASH_DISABLE=1 : bypass for a single command or shell
#
# Runs as root (install.sh already requires root context via incus exec).

set -euo pipefail

echo "[recycle-bin] installing trash-cli and shadow rm wrapper"

# 1. trash-cli
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends trash-cli

# 2. Point trash-cli at /var/recycle-bin (XDG_DATA_HOME/Trash convention)
mkdir -p /var/recycle-bin/files /var/recycle-bin/info
chmod 1777 /var/recycle-bin /var/recycle-bin/files /var/recycle-bin/info

# Global environment so all shells (including systemd, sudo) pick it up.
cat > /etc/profile.d/taos-recycle-bin.sh <<'EOF'
# Point trash-cli at /var/recycle-bin (system-wide taOS recycle bin).
export XDG_DATA_HOME=/var
EOF
chmod 644 /etc/profile.d/taos-recycle-bin.sh

# 3. Shadow /usr/local/bin/rm
cat > /usr/local/bin/rm <<'EOF'
#!/usr/bin/env bash
# taOS shadow rm — soft-delete via trash-put unless TAOS_TRASH_DISABLE=1 set.
# Invoke /usr/bin/rm directly for permanent delete without this shadow.
set -euo pipefail
if [ "${TAOS_TRASH_DISABLE:-0}" = "1" ]; then
  exec /usr/bin/rm "$@"
fi
# Pass through if no args or only flags (trash-put rejects; /usr/bin/rm handles usage errors).
HAS_PATHS=0
for a in "$@"; do
  case "$a" in
    -*) ;;
    *)  HAS_PATHS=1; break ;;
  esac
done
if [ "$HAS_PATHS" = "0" ]; then
  exec /usr/bin/rm "$@"
fi
# Route only the path operands through trash-put. Flags (-r, -f, etc.) are ignored
# because trash-put's semantics differ; but -r recursion is default for directories
# and -f suppresses errors by our choice.
for arg in "$@"; do
  case "$arg" in
    -*) ;;
    *)  trash-put -- "$arg" 2>/dev/null || /usr/bin/rm -f -- "$arg" ;;
  esac
done
EOF
chmod 755 /usr/local/bin/rm

# 4. 30-day retention sweep: /usr/local/bin/taos-recycle-sweep + systemd timer
cat > /usr/local/bin/taos-recycle-sweep <<'EOF'
#!/usr/bin/env bash
# Deletes items from /var/recycle-bin older than 30 days.
# Safe to run daily; idempotent.
set -euo pipefail
find /var/recycle-bin/files -mindepth 1 -mtime +30 -print0 2>/dev/null \
  | xargs -0 -r /usr/bin/rm -rf
find /var/recycle-bin/info -mindepth 1 -mtime +30 -type f -name '*.trashinfo' \
  -delete 2>/dev/null || true
EOF
chmod 755 /usr/local/bin/taos-recycle-sweep

cat > /etc/systemd/system/tinyagentos-recycle-sweep.service <<'EOF'
[Unit]
Description=taOS recycle-bin 30-day retention sweep

[Service]
Type=oneshot
ExecStart=/usr/local/bin/taos-recycle-sweep
EOF

cat > /etc/systemd/system/tinyagentos-recycle-sweep.timer <<'EOF'
[Unit]
Description=Daily taOS recycle-bin retention sweep

[Timer]
OnBootSec=30min
OnUnitActiveSec=24h
Persistent=true
Unit=tinyagentos-recycle-sweep.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now tinyagentos-recycle-sweep.timer

echo "[recycle-bin] ready; /usr/local/bin/rm now soft-deletes to /var/recycle-bin"
