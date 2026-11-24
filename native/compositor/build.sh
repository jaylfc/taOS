#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Reproducible build of the taOS desktop compositor (swayfx on wlroots 0.20)
# for Debian 13 trixie amd64.
#
#   PREFIX=/opt/taos/compositor ./build.sh <staging-dir> <work-dir> [-jN]
#
# Everything is installed with DESTDIR=<staging-dir>, i.e. the files land in
# <staging-dir>/<PREFIX>/... and nothing is written anywhere else. Every
# binary and library is linked with RPATH=<PREFIX>/lib so the carried
# libraries load without LD_LIBRARY_PATH once the tree is copied to PREFIX.
# Debian's own sway and system libraries are never touched.
#
# Build dependencies come from apt and are not carried: patch, meson, ninja-build,
# pkg-config, cmake, curl, a C compiler, and the -dev packages Debian's sway
# needs (libinput, xkbcommon, libseat, EGL/GLES/gbm, libliftoff,
# libdisplay-info, xcb, json-c, pcre2, cairo, pango, gdk-pixbuf, evdev,
# systemd). `apt build-dep sway` covers them. Debian's old wayland, libdrm,
# pixman and wayland-protocols are shadowed by the versions built here.
set -euo pipefail

PREFIX="${PREFIX:-/opt/taos/compositor}"
STAGE="${1:-${STAGE:-}}"
WORK="${2:-${WORK:-}}"
JOBS="${3:--j$(nproc)}"
[ -n "$STAGE" ] && [ -n "$WORK" ] || {
  echo "usage: PREFIX=$PREFIX $0 <staging-dir> <work-dir> [-jN]" >&2; exit 2; }
case "$PREFIX" in /*) ;; *) echo "PREFIX must be absolute" >&2; exit 2;; esac
PREFIX="${PREFIX%/}"
mkdir -p "$STAGE" "$WORK"
STAGE="$(cd "$STAGE" && pwd)"
WORK="$(cd "$WORK" && pwd)"
ROOT="$STAGE$PREFIX"            # where the tree really lives during the build
DL="$WORK/dl"; SRC="$WORK/src"; BLD="$WORK/build"; PCO="$WORK/pc-overlay"
mkdir -p "$DL" "$SRC" "$BLD"

# name|version|url|sha256
COMPONENTS=(
"wayland|1.24.0|https://gitlab.freedesktop.org/wayland/wayland/-/releases/1.24.0/downloads/wayland-1.24.0.tar.xz|82892487a01ad67b334eca83b54317a7c86a03a89cfadacfef5211f11a5d0536"
"wayland-protocols|1.50|https://gitlab.freedesktop.org/wayland/wayland-protocols/-/releases/1.50/downloads/wayland-protocols-1.50.tar.xz|4460c20cfcf68b994a5ee206ed15fc19ad312f16c96075186e83b0e3f4aa48a5"
"libdrm|2.4.131|https://dri.freedesktop.org/libdrm/libdrm-2.4.131.tar.xz|45ba9983b51c896406a3d654de81d313b953b76e6391e2797073d543c5f617d5"
"pixman|0.46.4|https://cairographics.org/releases/pixman-0.46.4.tar.gz|d09c44ebc3bd5bee7021c79f922fe8fb2fb57f7320f55e97ff9914d2346a591c"
"wlroots|0.20.2|https://gitlab.freedesktop.org/wlroots/wlroots/-/releases/0.20.2/downloads/wlroots-0.20.2.tar.gz|80c567d2ed4efb2cfa6b077f22c7a710d9c78ba4e185a75e8694001114af0733"
"scenefx|0.5|https://github.com/wlrfx/scenefx/archive/refs/tags/0.5.tar.gz|0fa8ecca0e310f813efd052624c5ed7d9153d6a0fdead5cc957d34c07f9a86c6"
"swayfx|0.6.1|https://github.com/WillPower3309/swayfx/archive/refs/tags/0.6.1.tar.gz|7afe1e29a3508d3ab724d5ef029df48fcbf770c4af48c294e3051571aa835dcb"
)

# Patches carried on top of a pinned tarball: component|patch file (relative to this script)|sha256 of the patch.
# Each is applied with `patch -p1` right after the tarball is unpacked; a checksum mismatch stops the build.
PATCHES=(
"swayfx|patches/swayfx-0.6.1-minimize-registration.patch|bb4eeb5cb6cd6f95956a816c9669a3fed0909d4b9b44ff7585036d5f748c267c"
)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# extra meson options per component
declare -A OPTS=(
  [wayland]="-Ddocumentation=false -Ddtd_validation=false -Dtests=false"
  [wayland-protocols]="-Dtests=false"
  [libdrm]="-Damdgpu=disabled -Dradeon=disabled -Dnouveau=disabled -Dintel=disabled -Dvmwgfx=disabled -Dfreedreno=disabled -Dvalgrind=disabled -Dman-pages=disabled -Dtests=false"
  [pixman]="-Ddemos=disabled -Dgtk=disabled -Dtests=disabled"
  [wlroots]="-Dexamples=false"
  [scenefx]="-Dexamples=false"
  [swayfx]="-Dman-pages=disabled"
)

# ---- fetch + verify ---------------------------------------------------------
fetch() {  # name version url sha
  local f
  case "$3" in *.tar.xz) f="$DL/$1-$2.tar.xz";; *) f="$DL/$1-$2.tar.gz";; esac
  if [ ! -f "$f" ] || ! echo "$4  $f" | sha256sum -c --status -; then
    echo "==> download $1 $2" >&2
    curl -fsSL --retry 3 -o "$f.part" "$3"
    mv "$f.part" "$f"
  fi
  echo "$4  $f" | sha256sum -c - >&2 || { echo "CHECKSUM MISMATCH: $1 $2" >&2; exit 1; }
  echo "$f"
}

# ---- environment for building against the staged (not yet installed) tree ---
# Staged .pc files say prefix=$PREFIX, which does not exist yet; keep an overlay
# copy with the staging dir prepended so meson/pkg-config find the new libs and
# tools. The overlay lives in $WORK only; the staged .pc files stay pristine.
refresh_overlay() {
  rm -rf "$PCO"; mkdir -p "$PCO"
  local d
  for d in "$ROOT/lib/pkgconfig" "$ROOT/share/pkgconfig"; do
    [ -d "$d" ] || continue
    for f in "$d"/*.pc; do
      [ -e "$f" ] || continue
      sed "s|$PREFIX|$ROOT|g" "$f" > "$PCO/$(basename "$f")"
    done
  done
}
export PKG_CONFIG_PATH="$PCO"
export PATH="$ROOT/bin:$PATH"
export LD_LIBRARY_PATH="$ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"   # build-time tools only
export LDFLAGS="-Wl,-rpath,$PREFIX/lib -Wl,--disable-new-dtags -Wl,-rpath-link,$ROOT/lib"
export CFLAGS="${CFLAGS:--O2}"
unset PKG_CONFIG_SYSROOT_DIR PKG_CONFIG_LIBDIR

for c in "${COMPONENTS[@]}"; do
  IFS='|' read -r name ver url sha <<<"$c"
  tarball="$(fetch "$name" "$ver" "$url" "$sha")"
  echo "==> build $name $ver"
  rm -rf "$SRC/$name-$ver" "$BLD/$name"
  mkdir -p "$SRC/$name-$ver"
  tar -xf "$tarball" -C "$SRC/$name-$ver" --strip-components=1
  for p in "${PATCHES[@]}"; do
    IFS='|' read -r pname pfile psha <<<"$p"
    [ "$pname" = "$name" ] || continue
    echo "$psha  $HERE/$pfile" | sha256sum -c --status - || { echo "PATCH CHECKSUM MISMATCH: $pfile" >&2; exit 1; }
    echo "==> patch $name: $pfile" >&2
    patch -p1 -d "$SRC/$name-$ver" < "$HERE/$pfile" > "$WORK/$name.patch.log" 2>&1 \
      || { cat "$WORK/$name.patch.log" >&2; exit 1; }
  done
  refresh_overlay
  # shellcheck disable=SC2086
  meson setup "$BLD/$name" "$SRC/$name-$ver" \
    --prefix="$PREFIX" --libdir=lib --buildtype=release ${OPTS[$name]} \
    > "$WORK/$name.setup.log" 2>&1 || { tail -30 "$WORK/$name.setup.log"; exit 1; }
  nice -n 10 ninja -C "$BLD/$name" "$JOBS" > "$WORK/$name.build.log" 2>&1 \
    || { tail -30 "$WORK/$name.build.log"; exit 1; }
  DESTDIR="$STAGE" ninja -C "$BLD/$name" install >> "$WORK/$name.build.log" 2>&1 \
    || { tail -30 "$WORK/$name.build.log"; exit 1; }
done

# ---- sanity checks ----------------------------------------------------------
echo "==> checks"
# 1. nothing was installed outside $PREFIX inside the stage
stray="$(cd "$STAGE" && find . -type f -o -type l | grep -v "^\./${PREFIX#/}/" || true)"
[ -z "$stray" ] || { echo "FILES OUTSIDE PREFIX:" >&2; echo "$stray" >&2; exit 1; }
# 2. staged .pc files must reference only PREFIX, never the staging dir
if grep -l "$STAGE" "$ROOT"/lib/pkgconfig/*.pc "$ROOT"/share/pkgconfig/*.pc 2>/dev/null; then
  echo "staging path leaked into .pc files" >&2; exit 1; fi
# 3. binaries present
for b in sway swaymsg swaynag swaybar; do
  [ -x "$ROOT/bin/$b" ] || { echo "missing $ROOT/bin/$b" >&2; exit 1; }
done
# 4. RPATH on the main binaries and libraries
for f in "$ROOT"/bin/sway "$ROOT"/bin/swaymsg "$ROOT"/lib/libwlroots-0.20.so "$ROOT"/lib/libscenefx-0.5.so; do
  [ -e "$f" ] || { echo "missing $f" >&2; exit 1; }
  readelf -d "$f" | grep -E "RPATH|RUNPATH" | grep -qF "[$PREFIX/lib]" \
    || { echo "RPATH not $PREFIX/lib in $f" >&2; exit 1; }
done
# 5. ldd: no carried library may resolve outside PREFIX (or its staged copy).
#    The staged copy of PREFIX does not exist at PREFIX yet, so point the loader
#    at it for this check only; on an installed tree plain ldd gives the same.
echo "==> ldd $PREFIX/bin/sway (carried libraries only)"
LDD="$(LD_LIBRARY_PATH="$ROOT/lib" ldd "$ROOT/bin/sway")"
echo "$LDD" | grep -E 'libwayland|libdrm|libpixman|libwlroots|libscenefx|not found' || true
bad="$(echo "$LDD" | grep -E 'libwayland|libdrm|libpixman|libwlroots|libscenefx|not found' \
  | grep -v -F -e "=> $ROOT/" -e "=> $PREFIX/" || true)"
if [ -n "$bad" ]; then
  echo "CARRIED LIBRARIES RESOLVING OUTSIDE $PREFIX:" >&2; echo "$bad" >&2; exit 1; fi
echo "carried libraries resolving outside $PREFIX: (none)"
echo "==> done: $ROOT"
