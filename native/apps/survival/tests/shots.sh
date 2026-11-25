#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Offscreen renders of the real taos-survival executable in a private home (nothing of anyone's own data, themes or
# running apps is touched: private HOME, XDG_*, TMPDIR and runtime dir; the offscreen platform opens no display).
#   shots.sh <apps build dir> <out dir> [theme ...]      (default theme: graphite; each is rendered dark and light)
# SURVIVAL_PACKS=<dir> links the .zim and .pmtiles files found there into the private packs folder, so the shots show
# real content; without it every section is rendered empty.  SIZES="1600x1000 412x915" picks the window sizes.
set -euo pipefail
B=$1; OUT=$2; shift 2
THEMES=${*:-graphite}
mkdir -p "$OUT"
H=$(mktemp -d)
export HOME=$H XDG_DATA_HOME=$H/data XDG_CONFIG_HOME=$H/config XDG_CACHE_HOME=$H/cache XDG_RUNTIME_DIR=$H/rt TMPDIR=$H/tmp
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$TMPDIR"; chmod 700 "$XDG_RUNTIME_DIR"
export TAOS_APPEARANCE_MIRROR_DIR=$H/mirror TAOS_SYSTEM_APPEARANCE=$H/etc-appearance.json TAOS_SYSTEM_THEME_DIR=$H/system-themes
mkdir -p "$H/mirror"
unset TAOS_APPEARANCE TAOS_THEME_DIR WAYLAND_DISPLAY DISPLAY
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=${QT_QUICK_BACKEND:-software} TAOS_SCREENSHOT_DELAY_MS=${DELAY:-2500}
export TAOS_URL=http://127.0.0.1:9   # no controller: the app never needs one
export TAOS_SURVIVAL_DIR=$H/survival TAOS_REDUCE_MOTION=1
mkdir -p "$TAOS_SURVIVAL_DIR/packs"
if [ -n "${SURVIVAL_PACKS:-}" ]; then
  for f in "$SURVIVAL_PACKS"/*.zim "$SURVIVAL_PACKS"/*.pmtiles; do [ -e "$f" ] && ln -sf "$f" "$TAOS_SURVIVAL_DIR/packs/$(basename "$f")"; done
fi
# SURVIVAL_STATE=<file>: a state.json (markers, contacts, a position) copied in, so the map shots show them
[ -n "${SURVIVAL_STATE:-}" ] && cp "$SURVIVAL_STATE" "$TAOS_SURVIVAL_DIR/state.json"
trap 'rm -rf "$H"' EXIT
APP=$(find "$B" -type f -name taos-survival -perm -u+x | head -n 1)
[ -n "$APP" ] || { echo "taos-survival not found under $B" >&2; exit 1; }
SIZES=${SIZES:-"1600x1000 412x915"}; SIZES=${SIZES//_/ }
shot() { # name size args...
  local name=$1 size=$2; shift 2
  TAOS_SCREENSHOT="$OUT/$name.png" TAOS_SCREENSHOT_SIZE=$size "$APP" "$@" >/dev/null 2>"$OUT/$name.log" || echo "shot $name failed ($?)"
}
for theme in $THEMES; do for variant in ${VARIANTS:-dark light}; do
  export TAOS_THEME=$theme TAOS_THEME_VARIANT=$variant
  for size in $SIZES; do
    k=$([ "${size%%x*}" -lt 760 ] && echo phone || echo desktop); p="$theme-$variant-$k"
    shot "library-$p" "$size" --section library
    shot "library-article-$p" "$size" --section library --article "${SURVIVAL_ARTICLE:-}"
    shot "library-packs-$p" "$size" --section library --tab packs
    shot "library-search-$p" "$size" --section library --search "${SURVIVAL_QUERY:-malaria}"
    shot "map-$p" "$size" --section map
    shot "map-search-$p" "$size" --section map --map-search "${SURVIVAL_PLACE:-glas}"
    shot "map-packs-$p" "$size" --section map --tab packs
    shot "taostak-$p" "$size" --section taostak
    shot "taostak-radios-$p" "$size" --section taostak --tab radios
    shot "taostak-messages-$p" "$size" --section taostak --tab messages
    shot "kit-$p" "$size" --section kit
    shot "agent-$p" "$size" --section agent
  done
done; done
ls -la "$OUT" | awk '{print $5, $9}' | grep png || true
