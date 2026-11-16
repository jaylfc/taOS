#!/bin/sh
# Build every package under distro/packages/ into distro/dist/.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out="$here/dist"
mkdir -p "$out"
rm -f "$out"/*.deb
for pkg in "$here"/packages/*/; do
    name=$(sed -n 's/^Package: //p' "$pkg/DEBIAN/control")
    ver=$(sed -n 's/^Version: //p' "$pkg/DEBIAN/control")
    arch=$(sed -n 's/^Architecture: //p' "$pkg/DEBIAN/control")
    dpkg-deb --root-owner-group --build "$pkg" "$out/${name}_${ver}_${arch}.deb" >/dev/null
    echo "$out/${name}_${ver}_${arch}.deb"
done
