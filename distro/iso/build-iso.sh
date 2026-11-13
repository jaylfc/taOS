#!/usr/bin/env bash
# Build taOS amd64 ISO from official Debian 13 netinst
# Usage: build-iso.sh --netinst <debian-13-amd64-netinst.iso> --out <taos-amd64.iso>

set -euo pipefail

usage() {
    cat <<EOF
Usage: $0 --netinst <debian-13-amd64-netinst.iso> --out <taos-amd64.iso>

Remasters the official Debian 13 netinst ISO with taOS preseed and first-boot files.
Requires: xorriso, cpio, gzip
EOF
    exit 1
}

check_tool() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: Required tool '$1' not found in PATH" >&2
        exit 2
    fi
}

# Check required tools
check_tool xorriso
check_tool cpio
check_tool gzip

NETINST_ISO=""
OUT_ISO=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --netinst)
            NETINST_ISO="$2"
            shift 2
            ;;
        --out)
            OUT_ISO="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            usage
            ;;
    esac
done

if [[ -z "$NETINST_ISO" || -z "$OUT_ISO" ]]; then
    echo "ERROR: Both --netinst and --out are required" >&2
    usage
fi

if [[ ! -f "$NETINST_ISO" ]]; then
    echo "ERROR: Netinst ISO not found: $NETINST_ISO" >&2
    exit 1
fi

# Get the directory of this script to find the taOS files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Create temp directory
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

TREE="$TMPDIR/tree"
mkdir -p "$TREE"

log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

log "Extracting ISO tree..."
xorriso -osirrox on -indev "$NETINST_ISO" -extract / "$TREE"

log "Making tree writable..."
chmod -R u+w "$TREE"

# Copy preseed.cfg to the tree so it's accessible during injection
cp "$SCRIPT_DIR/preseed.cfg" "$TREE/preseed.cfg"

# Function to inject preseed.cfg into an initrd.gz
inject_preseed() {
    local initrd_gz="$1"
    if [[ ! -f "$initrd_gz" ]]; then
        return 0
    fi
    log "Injecting preseed.cfg into $(basename "$initrd_gz")..."
    gunzip -f "$initrd_gz"
    local initrd="${initrd_gz%.gz}"
    # preseed.cfg is at the root of the extracted tree
    (cd "$TREE" && echo "preseed.cfg" | cpio -H newc -o -A -F "$initrd")
    gzip -f "$initrd"
}

log "Injecting preseed into initrd.gz files..."
inject_preseed "$TREE/install.amd/initrd.gz"
inject_preseed "$TREE/install.amd/gtk/initrd.gz"

log "Copying taOS files..."
mkdir -p "$TREE/taos"
cp "$REPO_ROOT/install.sh" "$TREE/taos/install.sh"
cp "$SCRIPT_DIR/taos-firstboot.sh" "$TREE/taos/taos-firstboot.sh"
cp "$SCRIPT_DIR/taos-firstboot.service" "$TREE/taos/taos-firstboot.service"

log "Regenerating md5sum.txt..."
cd "$TREE"
find . -type f ! -name "md5sum.txt" -print0 2>/dev/null | xargs -0 md5sum > md5sum.txt 2>/dev/null || true

log "Repacking ISO..."
xorriso -indev "$NETINST_ISO" -outdev "$OUT_ISO" -map "$TREE" / -boot_image any replay

log "Done! Output: $OUT_ISO"
