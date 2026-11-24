#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# install-desktop.sh: install the native taOS desktop (shell pieces, apps, themes, sway snippets) from build
# directories. Run by root. Idempotent. Staged: everything is checked in a temporary copy first and nothing on
# the system is touched until the checks pass. It never restarts or reloads anything; restart the session
# pieces afterwards with native/restart-desktop-pieces.sh (as the logged-in user).
#
#   install-desktop.sh --sway /opt/taos/compositor/bin/sway \
#       --shell-build DIR --apps-build DIR [--apps-build DIR]... [--root DIR] [--check-only]
#
#   --sway PATH         the real compositor binary (swayfx); the complete config is validated with it
#                       (`sway -C`, headless, no display) before anything is installed
#   --validate-with PATH  a plain sway to validate with when --sway cannot start (swayfx needs a GPU even for -C)
#   --shell-build DIR   the native/shell build directory (bar, overview, dock, window, display, theme,
#                       launcher, search, taos-shell-session)
#   --apps-build DIR    a build directory with taos-* apps in it (repeatable: apps, settings, activity)
#   --root DIR          install into DIR instead of / (a fake root for testing; the validation then reads
#                       DIR/etc/taos/sway/config). Without it the script must run as root.
#   --check-only        validate and stage, install nothing
#   --without-notifyd   remove taos-notifyd from the session components. Without either flag an install keeps
#                       whatever the person chose: it stays in TAOS_SHELL_COMPONENTS if it is already there
#   --with-notifyd      also start taos-notifyd (the notification server) with the session. Off by default: it has no
#                       popup yet, so it would take the notification bus name and show the person nothing
#
# What it installs (paths relative to the root):
#   /opt/taos/shell/bin/<piece>         the shell binaries and taos-shell-session, taos-window-session-hook
#   /usr/local/bin/<piece>              wrappers that source /etc/taos/shell.env and exec the real binary
#   /opt/taos/apps/bin + /usr/local/{bin,share}   the apps, through native/apps/install-apps.sh
#   /usr/share/taos/themes/<theme>/     the shipped themes
#   /etc/taos/sway.d/NN-*.conf          the sway snippets (swayfx layer_effects lines enabled; 05-session-env.conf
#                                       gives D-Bus activated services the Wayland session variables)
#   /etc/taos/sway/config               ONLY the line `$mod+space floating toggle` moves to `$mod+Shift+space`
#   /etc/taos/shell.env                 TAOS_SHELL_COMPONENTS="taos-bar taos-overview taos-dock taos-launcher taos-frame"
#                                       (add taos-notifyd with --with-notifyd; off by default until a popup exists)
# Backups, timestamped, next to what they back up: <dir>.bak-<stamp> for /opt/taos/shell/bin,
# /opt/taos/apps/bin and /etc/taos/sway.d, and <file>.bak-<stamp> for the two edited files. The last lines
# printed are the rollback command.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=""
sway=""
sway_fallback=""
shell_build=""
apps_builds=""
check_only=0
with_notifyd=0   # 1 = add, 0 = leave as it is, -1 = remove
while [ $# -gt 0 ]; do
    case $1 in
        --root) root=${2:?}; shift 2 ;;
        --sway) sway=${2:?}; shift 2 ;;
        --validate-with) sway_fallback=${2:?}; shift 2 ;;
        --shell-build) shell_build=${2:?}; shift 2 ;;
        --apps-build) apps_builds="$apps_builds ${2:?}"; shift 2 ;;
        --check-only) check_only=1; shift ;;
        --with-notifyd) with_notifyd=1; shift ;;
        --without-notifyd) with_notifyd=-1; shift ;;
        -h|--help) sed -n '2,37p' "$0"; exit 0 ;;
        *) echo "install-desktop: unknown option $1" >&2; exit 2 ;;
    esac
done
die() { echo "install-desktop: $*" >&2; exit 1; }
[ -n "$sway" ] || die "--sway <path to the compositor binary> is required"
[ -x "$sway" ] || die "$sway is not an executable"
[ -z "$sway_fallback" ] || [ -x "$sway_fallback" ] || die "$sway_fallback is not an executable"
[ -d "$shell_build" ] || die "--shell-build DIR is required"
[ -n "$apps_builds" ] || die "--apps-build DIR is required"
for d in $apps_builds; do [ -d "$d" ] || die "$d is not a directory"; done
if [ -z "$root" ] && [ "$(id -u)" -ne 0 ]; then die "run as root (or use --root DIR for a fake root)"; fi
if [ -n "$root" ]; then mkdir -p "$root"; root=$(cd "$root" && pwd); fi
shell_build=$(cd "$shell_build" && pwd)
stamp=$(date -u +%Y%m%d-%H%M%S)

R=$root
sway_conf=$R/etc/taos/sway/config
sway_d=$R/etc/taos/sway.d
shell_env=$R/etc/taos/shell.env
shell_bin=$R/opt/taos/shell/bin
apps_dir=$R/opt/taos/apps
themes_dir=$R/usr/share/taos/themes
local_bin=$R/usr/local/bin
components="taos-bar taos-overview taos-dock taos-launcher taos-frame"
# an update must never undo a person's choice: taos-notifyd stays in the line when it is already there
if [ "$with_notifyd" -eq 0 ] && [ -r "$shell_env" ] &&
    grep -E '^[[:space:]]*(export[[:space:]]+)?TAOS_SHELL_COMPONENTS=' "$shell_env" | tail -n 1 | grep -Eq '[" ]taos-notifyd([" ]|$)'; then
    with_notifyd=1
fi
[ "$with_notifyd" -ne 1 ] || components="$components taos-notifyd"
pieces="taos-bar taos-overview taos-dock taos-window taos-display taos-theme taos-launcher taos-search taos-frame taos-notifyd taos-appgroups"

stage=$(mktemp -d "${TMPDIR:-/tmp}/taos-install-stage.XXXXXX")
trap 'rm -rf "$stage"' EXIT

# ---------------------------------------------------------------- stage: binaries
find_exe() { # <dir> <name>: an executable file called <name> anywhere under <dir>, not in CMakeFiles
    find "$1" -type f -name "$2" -perm -u+x -not -path '*/CMakeFiles/*' 2>/dev/null | head -n 1
}
mkdir -p "$stage/bin"
for p in $pieces; do
    f=$(find_exe "$shell_build" "$p")
    [ -n "$f" ] || die "$p not found under $shell_build (build the shell first)"
    cp "$f" "$stage/bin/$p"
done
cp "$here/shell/taos-shell-session" "$stage/bin/taos-shell-session"
cp "$here/shell/window/session/taos-window-session-hook" "$stage/bin/taos-window-session-hook"
chmod 0755 "$stage"/bin/*
for a in taos-dashboard taos-notifications taos-decisions taos-settings taos-agents taos-messages; do
    ok=0
    for d in $apps_builds; do [ -n "$(find_exe "$d" "$a")" ] && ok=1; done
    [ "$ok" -eq 1 ] || die "$a not found under:$apps_builds"
done

# ---------------------------------------------------------------- stage: sway snippets
mkdir -p "$stage/sway.d"
snip() { # <source> <installed name>
    [ -r "$1" ] || die "missing snippet $1"
    # swayfx layer_effects: the dock snippet ships the line commented (plain sway rejects it); the installed
    # compositor is swayfx, so enable it. One line per namespace.
    sed -e 's/^#\(layer_effects "taos-dock" .*\)$/\1/' "$1" >"$stage/sway.d/$2"
}
snip "$here/shell/window/sway/05-session-env.conf" 05-session-env.conf
snip "$here/shell/overview/sway/40-overview.conf" 40-overview.conf
snip "$here/shell/sway/45-taos-display.conf" 45-taos-display.conf
snip "$here/shell/window/sway/46-taos-window.conf" 46-taos-window.conf
snip "$here/shell/sway/47-taos-dock.conf" 47-taos-dock.conf
snip "$here/shell/launcher/sway/48-taos-launcher.conf" 48-taos-launcher.conf
snip "$here/shell/frame/sway/49-taos-frame.conf" 49-taos-frame.conf
snip "$here/shell/sway/50-taos-audio-keys.conf" 50-taos-audio-keys.conf
for f in "$stage"/sway.d/*.conf; do
    if grep -E '^[[:space:]]*[^#[:space:]].*scratchpad_minimize|^[[:space:]]*scratchpad_minimize' "$f" >/dev/null; then
        die "$(basename "$f") sets scratchpad_minimize, which crashes the compositor on reload: refusing"
    fi
done
grep -q '^layer_effects "taos-dock" blur enable; blur_ignore_transparent enable; shadows enable; corner_radius 18$' \
    "$stage/sway.d/47-taos-dock.conf" || die "the dock snippet has no layer_effects line to enable"
# the same one-line-per-namespace form for the others
grep -q '^layer_effects "taos-launcher" ' "$stage/sway.d/48-taos-launcher.conf" || die "the launcher snippet lost its layer_effects line"

# ---------------------------------------------------------------- stage: the edited base config
[ -r "$sway_conf" ] || die "$sway_conf not found (is this taOS desktop installed?)"
# only the floating toggle binding moves; the launcher owns $mod+space
sed -E 's/^([[:space:]]*bindsym[[:space:]]+(--[A-Za-z-]+[[:space:]]+)*)(\$mod|Mod4|Super)\+space([[:space:]]+floating[[:space:]]+toggle[[:space:]]*)$/\1\3+Shift+space\4/' \
    "$sway_conf" >"$stage/sway-config"
n_changed=$(diff "$sway_conf" "$stage/sway-config" | grep -c '^>' || true)
[ "$n_changed" -le 1 ] || die "the base config edit would change $n_changed lines (expected at most 1)"

# ---------------------------------------------------------------- 1. validate the complete config
vroot=$stage/validate
mkdir -p "$vroot/sway.d" "$vroot/home/.config/taos/sway.d" "$vroot/run"
chmod 700 "$vroot/run"
# what /etc/taos/sway.d will hold: what is there now (minus files we replace) plus the new snippets
if [ -d "$sway_d" ]; then cp -a "$sway_d"/. "$vroot/sway.d/" 2>/dev/null || true; fi
cp "$stage"/sway.d/*.conf "$vroot/sway.d/"
# the config's absolute includes point into the temp copy; the person's own ~/.config is not read
sed -e "s|/etc/taos/sway.d|$vroot/sway.d|g" -e "s|~/.config/taos|$vroot/home/.config/taos|g" \
    -e "s|\$HOME/.config/taos|$vroot/home/.config/taos|g" "$stage/sway-config" >"$vroot/config"
# The GPU render node the headless validation start uses: TAOS_VALIDATE_RENDER_NODE, else the first one present.
vnode=${TAOS_VALIDATE_RENDER_NODE:-}
if [ -z "$vnode" ]; then
    for n in /dev/dri/renderD*; do [ -c "$n" ] && { vnode=$n; break; }; done
fi
if [ -z "$vnode" ] || [ ! -c "$vnode" ]; then
    echo "install-desktop: no GPU render node (/dev/dri/renderD*) found${TAOS_VALIDATE_RENDER_NODE:+, and TAOS_VALIDATE_RENDER_NODE=$TAOS_VALIDATE_RENDER_NODE is not a device}." >&2
    echo "  The compositor config is checked by starting the compositor headless, which needs a render node." >&2
    echo "  Set TAOS_VALIDATE_RENDER_NODE to one, or run on a machine with a GPU driver loaded." >&2
    exit 1
fi
validate_with() { # <sway binary>: sets vout, vrc, vread (1 when the compositor got as far as reading the config)
    vout=$(env -i PATH="$PATH" HOME="$vroot/home" XDG_RUNTIME_DIR="$vroot/run" WLR_BACKENDS=headless \
               WLR_RENDER_DRM_DEVICE=$vnode __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/50_mesa.json WLR_LIBINPUT_NO_DEVICES=1 ${LD_LIBRARY_PATH:+LD_LIBRARY_PATH="$LD_LIBRARY_PATH"} \
               "$1" -C -d -c "$vroot/config" 2>&1) && vrc=0 || vrc=$?
    if printf '%s\n' "$vout" | grep -q -E 'Config command|Loading config|Read line'; then vread=1; else vread=0; fi
}
used=$sway
validate_with "$sway"
if [ "$vread" -eq 0 ] && [ -n "$sway_fallback" ]; then
    echo "note: $sway exited before reading the config (swayfx needs a GPU to start); validating with $sway_fallback"
    used=$sway_fallback
    validate_with "$sway_fallback"
fi
if [ "$vread" -eq 0 ]; then
    echo "install-desktop: $used exited (status $vrc) before it read the config, so nothing was validated; nothing was installed." >&2
    echo "  swayfx creates its GL renderer first and needs a GPU. Run this on the desktop itself, or pass --validate-with <plain sway>." >&2
    printf '%s\n' "$vout" | tail -n 3 >&2
    exit 1
fi
# plain sway does not know swayfx's commands: those are not defects of the snippets (the real swayfx knows them)
fx='layer_effects|blur|blur_|corner_radius|shadows|shadow_|default_dim_inactive|dim_inactive'
if "$used" --version 2>&1 | grep -qi swayfx; then fx='^$^'; fi
bad=$(printf '%s\n' "$vout" | grep -E 'Error on line|Overwriting binding|Duplicate' | grep -v -E "Unknown/invalid command '($fx)" || true)
if [ -n "$bad" ] || { [ "$vrc" -ne 0 ] && printf '%s\n' "$vout" | grep -E 'Error on line' | grep -q -v -E "Unknown/invalid command '($fx)"; }; then
    echo "install-desktop: the complete sway config does not validate (exit $vrc); nothing was installed:" >&2
    printf '%s\n' "${bad:-$vout}" | head -n 20 >&2
    exit 1
fi
echo "ok: the complete sway config validates with $used (no errors, no duplicate bindings)"
if [ "$check_only" -eq 1 ]; then echo "check only: nothing installed"; exit 0; fi

# ---------------------------------------------------------------- 2. backups
backup() { # <path>: copy a dir or file to <path>.bak-<stamp>; records it for the rollback
    [ -e "$1" ] || { echo "absent $1" >>"$stage/backups"; return 0; }
    cp -a "$1" "$1.bak-$stamp"
    echo "saved $1" >>"$stage/backups"
}
: >"$stage/backups"
mkdir -p "$R/etc/taos" "$R/opt/taos"
backup "$shell_bin"
backup "$apps_dir/bin"
backup "$sway_d"
backup "$sway_conf"
backup "$shell_env"

# ---------------------------------------------------------------- 3. install
changes="$stage/changes"
: >"$changes"
put() { # <src> <dest> <mode>: copy beside, then rename, so a running copy keeps working
    mkdir -p "$(dirname "$2")"
    if [ ! -e "$2" ]; then echo "new $2" >>"$changes"; elif ! cmp -s "$1" "$2"; then echo "changed $2" >>"$changes"; fi
    cp "$1" "$2.new"
    chmod "$3" "$2.new"
    mv -f "$2.new" "$2"
}
mkdir -p "$shell_bin" "$local_bin"
for f in "$stage"/bin/*; do
    n=$(basename "$f")
    put "$f" "$shell_bin/$n" 0755
    # a wrapper: the Qt prefix and plugin paths come from shell.env, like the apps' wrappers
    sed -e "s|@NAME@|$n|g" -e "s|@APP_DIR@|/opt/taos/shell|g" "$here/apps/packaging/app-wrapper.in" >"$stage/wrapper"
    put "$stage/wrapper" "$local_bin/$n" 0755
done
echo "installed shell binaries to $shell_bin, wrappers to $local_bin"

( cd "$apps_dir/bin" 2>/dev/null && md5sum -- * 2>/dev/null ) >"$stage/apps-before" || true
args=""
for d in $apps_builds; do args="$args --bin-dir $d"; done
# shellcheck disable=SC2086
sh "$here/apps/install-apps.sh" "$R/usr/local" --app-dir "$apps_dir" --runtime-app-dir /opt/taos/apps --no-icon-cache $args

( cd "$apps_dir/bin" 2>/dev/null && md5sum -- * 2>/dev/null ) >"$stage/apps-after" || true
mkdir -p "$themes_dir"
for t in "$here"/shell/common/resources/themes/*/; do
    n=$(basename "$t")
    mkdir -p "$themes_dir/$n"
    if [ ! -e "$themes_dir/$n/theme.json" ]; then echo "new $themes_dir/$n/theme.json" >>"$changes"
    elif ! cmp -s "$t"theme.json "$themes_dir/$n/theme.json"; then echo "changed $themes_dir/$n/theme.json" >>"$changes"; fi
    cp "$t"theme.json "$themes_dir/$n/theme.json.new"
    mv -f "$themes_dir/$n/theme.json.new" "$themes_dir/$n/theme.json"
done
# themes that ship as folders (Workbench, Parallax): the whole folder, wallpapers and fonts included, replaced file by file
for t in "$here"/shell/themes/*/; do
    [ -f "$t"theme.json ] || continue
    n=$(basename "$t")
    mkdir -p "$themes_dir/$n"
    (cd "$t" && find . -type f) | while IFS= read -r f; do
        mkdir -p "$themes_dir/$n/$(dirname "$f")"
        if [ ! -e "$themes_dir/$n/$f" ]; then echo "new $themes_dir/$n/$f" >>"$changes"
        elif ! cmp -s "$t$f" "$themes_dir/$n/$f"; then echo "changed $themes_dir/$n/$f" >>"$changes"; fi
        cp "$t$f" "$themes_dir/$n/$f.new"
        mv -f "$themes_dir/$n/$f.new" "$themes_dir/$n/$f"
    done
done
echo "installed themes to $themes_dir"
# Fonts a theme bundles (themes/<id>/fonts/*.ttf): fontconfig must know them so the compositor's title bars (pango) can use
# them too. One drop-in names the theme folder; no font is copied anywhere else.
fc_dir=$R/etc/fonts/conf.d
mkdir -p "$fc_dir"
cat >"$stage/fc.conf" <<'FC'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<!-- Installed by native/install-desktop.sh: the fonts the taOS themes bundle (usr/share/taos/themes/<id>/fonts). -->
<fontconfig>
  <dir>/usr/share/taos/themes</dir>
</fontconfig>
FC
put "$stage/fc.conf" "$fc_dir/60-taos-theme-fonts.conf" 0644
if [ -z "$root" ] && command -v fc-cache >/dev/null 2>&1; then
    fc-cache -s "$themes_dir" 2>/dev/null && echo "fontconfig cache refreshed for $themes_dir"
fi

mkdir -p "$sway_d"
for f in "$stage"/sway.d/*.conf; do put "$f" "$sway_d/$(basename "$f")" 0644; done
echo "installed sway snippets to $sway_d"
if [ "$n_changed" -eq 1 ]; then
    put "$stage/sway-config" "$sway_conf" 0644
    echo "moved \$mod+space floating toggle to \$mod+Shift+space in $sway_conf"
else
    echo "$sway_conf: no \$mod+space floating toggle line to move (already moved, or bound otherwise)"
fi

mkdir -p "$(dirname "$shell_env")"
touch "$shell_env"
if grep -Eq '^[[:space:]]*(export[[:space:]]+)?TAOS_SHELL_COMPONENTS=' "$shell_env"; then
    sed -E "s|^([[:space:]]*(export[[:space:]]+)?)TAOS_SHELL_COMPONENTS=.*\$|\\1TAOS_SHELL_COMPONENTS=\"$components\"|" "$shell_env" >"$stage/shell.env"
else
    { cat "$shell_env"; echo "TAOS_SHELL_COMPONENTS=\"$components\""; } >"$stage/shell.env"
fi
put "$stage/shell.env" "$shell_env" 0644
echo "TAOS_SHELL_COMPONENTS=\"$components\" in $shell_env"

# ---------------------------------------------------------------- 3b. what applies how
apps_changed=$(diff "$stage/apps-before" "$stage/apps-after" 2>/dev/null | sed -n 's/^> [0-9a-f]*  *//p' | tr '\n' ' ')
bins_changed=$(sed -n "s#^\(changed\|new\) $shell_bin/##p" "$changes" | grep -v '\.new$' | tr '\n' ' ')
snips_changed=$(sed -n "s#^\(changed\|new\) $sway_d/##p" "$changes" | tr '\n' ' ')
data_changed=$(grep -c " $themes_dir/\| $fc_dir/" "$changes" || true)
echo
echo "=== what this changed, and what it needs ==="
echo "Running processes keep what they started with. Nothing was restarted or reloaded."
if [ -n "$snips_changed" ]; then
    echo "COMPOSITOR CONFIG RELOAD needed (swaymsg reload, as the logged-in user, or restart-desktop-pieces.sh --reload):"
    echo "  sway snippets changed: $snips_changed"
    echo "  new for_window rules apply to windows opened after the reload; 'exec' lines (not exec_always) do not run again."
else
    echo "compositor config reload: not needed (no sway snippet changed)"
fi
pieces_restart=""
for c in $components; do case " $bins_changed " in *" $c "*) pieces_restart="$pieces_restart $c";; esac; done
if [ -n "$pieces_restart" ]; then
    echo "SHELL PIECES RESTART needed (restart-desktop-pieces.sh, as the logged-in user; no window is closed):"
    echo "  changed:$pieces_restart"
else
    echo "shell pieces restart: not needed (no bar, overview, dock, launcher or frame binary changed)"
fi
case " $bins_changed " in *" taos-window "*) echo "taos-window changed: the running mode daemon keeps the old binary until the session is restarted (restart-desktop-pieces.sh never restarts it).";; esac
case " $bins_changed " in *" taos-window-session-hook "*) echo "taos-window-session-hook changed: the running watcher keeps the old copy; stop it by its pid (it is a sh script named taos-window-session-hook) and start the new one, or log out and in. The new copy is single-instance and exits when its compositor socket is gone.";; esac
case " $bins_changed " in *" taos-theme "*|*" taos-appgroups "*|*" taos-display "*|*" taos-search "*) echo "command line tools changed (${bins_changed}): used by the next command, nothing to restart.";; esac
if [ -n "$apps_changed" ]; then
    echo "APPS changed: $apps_changed"
    echo "  open copies keep running the old binary. Close them and open them again."
    echo "  The new apps keep their single-instance socket in \$XDG_RUNTIME_DIR/taos (the old ones used /tmp): while an OLD copy is"
    echo "  still open, starting the app again opens a SECOND copy instead of raising the first. Close old windows first."
fi
if [ "$data_changed" -gt 0 ]; then
    echo "themes, wallpapers and fonts: $data_changed file(s) changed. No restart: the theme list is read when a theme is chosen."
    echo "  Bundled fonts reach the compositor's title bars through fontconfig; sway reads fonts when its config loads, so they show after the reload above."
fi
echo "============================================="

# ---------------------------------------------------------------- 4. rollback
rb=$R/opt/taos/desktop-rollback-$stamp.sh
{
    echo '#!/bin/sh'
    echo "# Undo install-desktop.sh ($stamp): put the backed-up directories and files back."
    echo "# The current ones are kept as <name>.rolledback-$stamp. Nothing is restarted."
    echo 'set -eu'
    while read -r kind path; do
        if [ "$kind" = saved ]; then
            echo "[ ! -e '$path' ] || mv '$path' '$path.rolledback-$stamp'"
            echo "cp -a '$path.bak-$stamp' '$path'"
        else
            echo "[ ! -e '$path' ] || mv '$path' '$path.rolledback-$stamp'"
        fi
    done <"$stage/backups"
    echo "echo 'rolled back. Run restart-desktop-pieces.sh to start the old pieces.'"
} >"$rb"
chmod 0755 "$rb"
echo
echo "Nothing was restarted or reloaded. To use the new pieces, as the logged-in user:"
echo "  sh $here/restart-desktop-pieces.sh [--reload]"
echo "Rollback:"
echo "  sh $rb"
