#!/bin/bash
# Triggers taOS graceful shutdown via HTTP. Used by systemd stop/pre-shutdown hooks.
# Succeeds even if the API is unreachable so we don't block system reboot.
#
# --max-time is deliberately short: this runs on every `systemctl restart`, and
# if /api/system/prepare-shutdown ever hangs (it has), a long timeout strands the
# service in `deactivating` with the port dead for minutes — which also makes the
# in-app Update appear to fail, since it restarts the service. Draining must be
# best-effort and quick; anything slower belongs in an async background task.
#
# Honour the configured controller port: systemd passes TAOS_PORT into this
# hook's environment (see install-server.sh), so a custom-port install drains
# the right origin instead of a hardcoded 6969 that would silently no-op.
#
# Dedupe: both the unit ExecStop and taos-pre-shutdown.service call this script
# on a reboot. Write a stamp on success so a second invocation within 60s is
# a no-op, avoiding a double agent prepare-shutdown pass.
#
# The stamp is a kill switch, so it has to live where only this service can
# write it. It used to fall back to /tmp whenever /run was unwritable (that is,
# on every non-root install), and /tmp is world-writable: the sticky bit blocks
# deleting another user's file, not creating one. Any local user could plant the
# stamp, keep it fresh from cron, and permanently suppress the drain — silently,
# because the script believes a sibling invocation already stamped. On hardware
# with this filesystem's writeback history, skipping the drain is the worst
# available failure. Candidate directories, best first:
#   1. $RUNTIME_DIRECTORY — set by systemd from RuntimeDirectory=taos (0750):
#      /run/taos for the system unit, $XDG_RUNTIME_DIR/taos for the user unit.
#   2. /run/taos — the same directory when the reboot hook runs outside the
#      unit; RuntimeDirectoryPreserve=yes keeps it across a restart.
#   3. $TAOS_INSTALL_DIR/data — the no-systemd nohup install; already 0700.
#   4. the checkout's data/ — when the script is run straight out of a source tree.
# If none of them qualifies we drop the dedupe and drain twice rather than trust
# a stamp anyone could have written.

# Portable (no GNU-only `stat`): a directory we can write into that neither the
# group nor other users can write to.
#
# Known gap: this only reads the POSIX mode bits `ls -ld` prints. A directory
# whose numeric mode is 0750 but which also carries a filesystem ACL granting
# write to a group or "other" (systemd's RuntimeDirectoryMode does not strip
# pre-existing ACLs, and some hardening profiles set default ACLs on /run)
# renders as `drwxr-x---+` here -- the trailing `+` is ignored and the ACL
# grant is invisible to this check. We don't shell out to `getfacl`/`setfacl`
# to close that gap because neither is guaranteed installed on every target
# (minimal container/embedded images in particular), and a missing-binary
# failure here must not silently fall through to a worse default. If a
# platform is known to set default ACLs on RuntimeDirectory, that must be
# fixed at the installer/systemd-unit level, not detected here.
stamp_dir_is_private() {
    [ -d "$1" ] && [ -w "$1" ] || return 1
    # drwxr-x---: chars 5-7 are the group bits, 8-10 the other bits.
    case "$(ls -ld "$1" 2>/dev/null | awk '{print $1}')" in
        ?????w*|????????w*) return 1 ;;
    esac
    return 0
}

# systemd hands over a colon-separated list when several RuntimeDirectory= are
# declared; ours is a single entry, but take the first defensively.
STAMP_FILE=""
for stamp_dir in \
    "${RUNTIME_DIRECTORY%%:*}" \
    /run/taos \
    "${TAOS_INSTALL_DIR:-$HOME/tinyagentos}/data" \
    "$(dirname "$0")/../data"
do
    if [ -n "$stamp_dir" ] && stamp_dir_is_private "$stamp_dir"; then
        STAMP_FILE="$stamp_dir/prepare-shutdown.stamp"
        break
    fi
done

if [ -n "$STAMP_FILE" ] && [ -r "$STAMP_FILE" ]; then
    # The stamp carries its own epoch. `stat -c %Y` is GNU-only: on macOS/BSD it
    # errors out, the `|| echo 0` then made every stamp look ancient, and the
    # dedupe never fired at all.
    stamp_epoch=$(head -n 1 "$STAMP_FILE" 2>/dev/null)
    case "$stamp_epoch" in
        '' | *[!0-9]*) stamp_epoch=0 ;;
    esac
    # A future-dated epoch (RTC-less Pis routinely step the clock forward by
    # minutes to hours on the first NTP sync after a power cut) must not
    # dedupe indefinitely: without the lower bound, a negative stamp_age is
    # always "-lt 60" and the drain stays suppressed until the wall clock
    # catches up to stamp_epoch + 60 -- on the exact failure mode this dedupe
    # exists to prevent.
    stamp_age=$(( $(date +%s) - stamp_epoch ))
    if [ "$stamp_age" -ge 0 ] && [ "$stamp_age" -lt 60 ]; then
        exit 0
    fi
fi

if curl -fsS -X POST --max-time 25 "http://localhost:${TAOS_PORT:-6969}/api/system/prepare-shutdown"; then
    # Only a successful prepare earns the dedupe stamp; a failed attempt must
    # not let the next invocation skip draining.
    if [ -n "$STAMP_FILE" ]; then
        (umask 077; date +%s > "$STAMP_FILE") 2>/dev/null || true
    fi
fi
exit 0
