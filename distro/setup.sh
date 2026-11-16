#!/bin/sh
# taOS desktop setup: the single source of truth that turns a minimal Debian 13
# (trixie) install into taOS. The amd64 ISO runs this same script. Idempotent:
# safe to re-run after every pull. Run as root from a checkout of this repo.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
if [ "$(id -u)" -ne 0 ]; then
    echo "setup.sh: run as root (sudo $0)" >&2
    exit 1
fi

# Step 1: identity (taos-branding).
"$here/build-debs.sh" >/dev/null
apt-get install -y --reinstall "$here"/dist/taos-branding_*_all.deb
. /etc/os-release
echo "setup.sh: now running $PRETTY_NAME"
