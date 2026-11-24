#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Loads the base config's keys plus every taos sway snippet together in sway (`sway -C`, validate only) and fails on
# any config error or any overwritten (duplicate) binding. Usage: check-sway-config.sh <snippet-root> [base.conf]
# Environment: TAOS_TEST_SWAY (default: sway on PATH), TAOS_TEST_SWAY_LIBS, TAOS_BASE_SWAY_CONFIG.
# Exit 77 (skipped) when there is no sway.
root=$1
base=${TAOS_BASE_SWAY_CONFIG:-$2}
sway=${TAOS_TEST_SWAY:-$(command -v sway)}
if [ -z "$sway" ] || [ ! -x "$sway" ]; then echo "SKIPPED: no sway binary (set TAOS_TEST_SWAY)"; exit 77; fi
[ -n "$TAOS_TEST_SWAY_LIBS" ] && LD_LIBRARY_PATH=$TAOS_TEST_SWAY_LIBS:$LD_LIBRARY_PATH && export LD_LIBRARY_PATH
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
export XDG_RUNTIME_DIR=$tmp WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1
cfg=$tmp/config
cp "$base" "$cfg" || exit 1
n=0
root=$(cd "$root" && pwd)
# scratchpad_minimize crashes the compositor on reload: no snippet may set it (comments and docs may name it).
hit=$(cd "$root" && find . -path '*/sway/*.conf' -not -path '*/tests/*' -not -path '*/build/*' -exec grep -H -E '^[[:space:]]*[^#[:space:]].*scratchpad_minimize|^[[:space:]]*scratchpad_minimize' {} + 2>/dev/null)
[ -z "$hit" ] || { echo "a snippet sets scratchpad_minimize (crashes the compositor):"; echo "$hit"; exit 1; }
for f in $(cd "$root" && find . -path '*/sway/*.conf' -not -path '*/tests/*' -not -path '*/build/*' | sort); do
    echo "include $root/$f" >> "$cfg"
    n=$((n + 1))
done
[ "$n" -gt 0 ] || { echo "no sway snippets found under $root"; exit 1; }
out=$("$sway" -C -d -c "$cfg" 2>&1)
rc=$?
# swayfx creates its GL renderer before it reads the config and ignores WLR_RENDERER=pixman: without a usable GPU it
# exits having read nothing, and "no errors" would be a vacuous pass. Skip (77) instead of passing.
if ! printf '%s\n' "$out" | grep -q -E 'Config command|Loading config|Read line'; then
    echo "SKIPPED: $sway read no config (exit $rc): it needs a GPU to start (swayfx); use plain sway for this check"
    printf '%s\n' "$out" | tail -n 3
    exit 77
fi
# Commands only swayfx knows (the carried compositor) are an error in plain sway: not a defect of the snippets.
fx='layer_effects|blur|blur_|corner_radius|shadows|shadow_|scratchpad_minimize|default_dim_inactive|dim_inactive|for_window.*blur'
bad=$(printf '%s\n' "$out" | grep -E "Error on line|Overwriting binding|Duplicate" | grep -v -E "Unknown/invalid command '($fx)")
if [ -n "$bad" ] || { [ "$rc" -ne 0 ] && printf '%s\n' "$out" | grep -E "Error on line" | grep -q -v -E "Unknown/invalid command '($fx)"; }; then
    echo "sway config check FAILED (exit $rc) for $n snippets:"
    printf '%s\n' "$bad"
    exit 1
fi
echo "ok: $n snippets load with the base keys, no errors, no duplicate bindings"
