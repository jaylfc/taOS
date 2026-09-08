#!/usr/bin/env bash
# library-vmaf-eval.sh — compute VMAF per (source, variant) pair via ffmpeg libvmaf.
# Measurement-only harness for the Library P4 research spike.
#
# Usage:
#   ./library-vmaf-eval.sh <config.json>
#
# Config format:
#   {
#     "pairs": [
#       {"video": "name", "source": "path", "variant": "path"},
#       ...
#     ]
#   }
#
# Output CSV (stdout):
#   video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct

set -uo pipefail

CONFIG="${1:?usage: $0 <config.json>}"

if [[ ! -f "$CONFIG" ]]; then
    echo "config not found: $CONFIG" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

resolve_path() {
    local p="$1"
    if [[ "$p" != /* ]]; then
        printf '%s/%s' "$SCRIPT_DIR/.." "$p"
    else
        printf '%s' "$p"
    fi
}

printf 'video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct\n'

TMPFLAG=$(mktemp)

python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
for pair in data.get('pairs', []):
    print('\t'.join([pair['video'], pair['source'], pair['variant']]))
" "$CONFIG" | while IFS=$'\t' read -r video source variant; do
    source_path="$(resolve_path "$source")"
    variant_path="$(resolve_path "$variant")"

    if [[ ! -f "$source_path" ]]; then
        echo "source not found: $source_path" >&2
        continue
    fi
    if [[ ! -f "$variant_path" ]]; then
        echo "variant not found: $variant_path" >&2
        continue
    fi

    bytes_source="$(stat -c%s "$source_path")"
    bytes_variant="$(stat -c%s "$variant_path")"

    ffmpeg_exit=0
    vmaf_output="$(ffmpeg -hide_banner -i "$source_path" -i "$variant_path" \
        -lavfi "[0:v][1:v]libvmaf" -f null - 2>&1)" || ffmpeg_exit=$?

    if [[ $ffmpeg_exit -ne 0 ]]; then
        echo "ffmpeg failure: $source_path / $variant_path" >&2
        echo "1" > "$TMPFLAG"
        continue
    fi

    vmaf_mean="$(echo "$vmaf_output" | grep 'VMAF score:' | awk '{print $NF}' | tail -1)"

    saving_pct="$(python3 -c "print(f'{(1 - ${bytes_variant} / ${bytes_source}) * 100:.2f}')")"

    if [[ -z "$vmaf_mean" ]]; then
        echo "1" > "$TMPFLAG"
        printf '%s,%s,ERROR,%s,%s,ERROR\n' \
            "$video" "$(basename "$variant")" \
            "$bytes_source" "$bytes_variant"
        continue
    fi

    printf '%s,%s,%s,%s,%s,%s\n' \
        "$video" "$(basename "$variant")" "$vmaf_mean" \
        "$bytes_source" "$bytes_variant" "$saving_pct"
done

if [[ "$(cat "$TMPFLAG" 2>/dev/null)" == "1" ]]; then
    rm -f "$TMPFLAG"
    exit 1
fi

rm -f "$TMPFLAG"
