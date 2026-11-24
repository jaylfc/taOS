#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# install-desktop.sh must never undo a person's notifyd choice:
#   fresh plain install            -> taos-notifyd not in TAOS_SHELL_COMPONENTS
#   --with-notifyd                 -> in
#   opted in, then a plain install -> still in
#   --without-notifyd              -> out (and a later plain install keeps it out)
# Runs the real installer into a fake root with fake binaries and a fake compositor.
set -u
here=$(cd "$(dirname "$0")" && pwd)
native=$(cd "$here/../../.." && pwd)
fails=0
ok() { echo "ok: $*"; }
bad() { echo "FAIL: $*"; fails=$((fails + 1)); }

tmp=$(mktemp -d "${TMPDIR:-/tmp}/taos-installer-test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/shell" "$tmp/apps" "$tmp/root/etc/taos/sway"
for p in taos-bar taos-overview taos-dock taos-window taos-display taos-theme taos-launcher taos-search taos-frame taos-notifyd taos-appgroups; do
    printf '#!/bin/sh\nexit 0\n' >"$tmp/shell/$p"; chmod 755 "$tmp/shell/$p"
done
for a in taos-dashboard taos-notifications taos-decisions taos-settings taos-agents taos-messages taos-store; do
    printf '#!/bin/sh\nexit 0\n' >"$tmp/apps/$a"; chmod 755 "$tmp/apps/$a"
done
printf '#!/bin/sh\necho "Loading config"\nexit 0\n' >"$tmp/sway"; chmod 755 "$tmp/sway"
printf 'set $mod Mod4\nbindsym $mod+space floating toggle\n' >"$tmp/root/etc/taos/sway/config"

install() { sh "$native/install-desktop.sh" --root "$tmp/root" --sway "$tmp/sway" --shell-build "$tmp/shell" \
    --apps-build "$tmp/apps" "$@" >"$tmp/out" 2>&1 || { cat "$tmp/out"; bad "install-desktop.sh $* failed"; }; }
line() { grep -E '^TAOS_SHELL_COMPONENTS=' "$tmp/root/etc/taos/shell.env" | tail -n 1; }
has() { line | grep -q 'taos-notifyd'; }

install
has && bad "a fresh plain install added taos-notifyd: $(line)" || ok "fresh plain install does not add taos-notifyd"
install --with-notifyd
has && ok "--with-notifyd adds it" || bad "--with-notifyd did not add it: $(line)"
install
has && ok "a plain install after opting in keeps it" || bad "a plain install removed the person's choice: $(line)"
install --without-notifyd
has && bad "--without-notifyd left it in: $(line)" || ok "--without-notifyd removes it"
install
has && bad "a plain install re-added it after --without-notifyd: $(line)" || ok "a plain install after --without-notifyd keeps it out"

[ "$fails" -eq 0 ] || { echo "$fails failure(s)"; exit 1; }
echo "all installer notifyd checks passed"
