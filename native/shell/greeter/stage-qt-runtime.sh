#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copy the part of a Qt installation that taos-greeter needs into one directory
# that an unprivileged system user can read (for example /opt/taos/qt).
#
#   stage-qt-runtime.sh <qt-prefix> <taos-greeter-binary> <dest> [<extra-lib-dir>]
#
# <qt-prefix> is the directory that has lib/, plugins/ and qml/ (for example
# <qt-root>/6.12.0/gcc_64). WebEngine, Designer, Quick Controls, Quick 3D and the
# rest are not copied: only the libraries the binary and the chosen plugins link
# to, plus the Wayland and image plugins and the Quick, Effects and Shapes QML
# modules. The result is verified by running `taos-greeter --version` with the
# environment pointing only at <dest>.
set -eu
qt=${1:?qt prefix}; bin=${2:?taos-greeter binary}; dest=${3:?destination}; extra=${4:-}
[ -d "$qt/lib" ] && [ -d "$qt/plugins" ] && [ -d "$qt/qml" ] || { echo "not a Qt prefix: $qt" >&2; exit 2; }
mkdir -p "$dest/lib" "$dest/plugins" "$dest/qml"

LD_LIBRARY_PATH="$qt/lib${extra:+:$extra}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH

copy_libs_of() { # file : copies each dependency that lives under the Qt prefix (or the extra dir)
    ldd "$1" 2>/dev/null | awk '/=>/ {print $3}' | while read -r l; do
        keep=0
        case $l in "$qt"/lib/*) keep=1 ;; esac
        if [ -n "$extra" ]; then case $l in "$extra"/*) keep=1 ;; esac; fi
        [ "$keep" = 1 ] || continue
        cp -Ln "$l" "$dest/lib/$(basename "$l")" 2>/dev/null || true
    done
}
copy_plugin() { # relative path under plugins/
    [ -e "$qt/plugins/$1" ] || return 0
    mkdir -p "$dest/plugins/$(dirname "$1")"
    cp -an "$qt/plugins/$1" "$dest/plugins/$1"
    copy_libs_of "$qt/plugins/$1"
}
copy_libs_of "$bin"
for p in platforms/libqwayland.so \
         wayland-graphics-integration-client/*.so \
         wayland-shell-integration/*.so \
         wayland-decoration-client/*.so \
         imageformats/libqjpeg.so imageformats/libqpng.so imageformats/libqsvg.so imageformats/libqgif.so \
         platforminputcontexts/libcomposeplatforminputcontextplugin.so \
         tls/*.so iconengines/*.so; do
    for f in $(cd "$qt/plugins" && ls $p 2>/dev/null); do copy_plugin "$f"; done
done
# QML: the engine, Quick, Effects, Shapes and what they need to load.
copy_qml_dir() { [ -d "$qt/qml/$1" ] && { mkdir -p "$dest/qml/$(dirname "$1")"; cp -an "$qt/qml/$1" "$dest/qml/$(dirname "$1")/"; }; }
copy_qml_dir QtQml
rm -rf "$dest/qml/QtQml/XmlListModel"
copy_qml_dir QtCore
mkdir -p "$dest/qml/QtQuick"
for m in "$qt"/qml/QtQuick/*; do
    case $(basename "$m") in
        Controls|Dialogs|NativeStyle|Templates|VirtualKeyboard|Pdf|Scene2D|Scene3D|Designer|tooling|LocalStorage|Timeline|Studio*|Particles) continue ;;
    esac
    cp -an "$m" "$dest/qml/QtQuick/"
done
cp -an "$qt"/qml/QtQuick/qmldir "$dest/qml/QtQuick/" 2>/dev/null || true
for f in $(find "$dest/qml" -name '*.so'); do copy_libs_of "$f"; done
# Plugins loaded by QML modules may pull further libraries: one more sweep.
for f in $(find "$dest/plugins" -name '*.so'); do copy_libs_of "$f"; done

# Verify: run with the environment pointing ONLY at the staged runtime.
env -i LD_LIBRARY_PATH="$dest/lib" QT_PLUGIN_PATH="$dest/plugins" QML_IMPORT_PATH="$dest/qml" QT_QPA_PLATFORM=offscreen \
    "$bin" --version
du -sh "$dest"
