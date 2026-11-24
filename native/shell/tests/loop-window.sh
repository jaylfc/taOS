#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Test client content for e2e-overview.sh: a big title and a counter redrawn in place, so a live
# thumbnail visibly changes. usage: loop-window.sh <title>
T=$1; i=0
printf '\033[2J'
while true; do
    printf '\033[H\n  %s\n\n  %s\n\n  tick %d\n' "$T" "$(date +%T)" "$i"
    i=$((i + 1))
    sleep 0.1
done
