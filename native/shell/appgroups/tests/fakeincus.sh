#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# A stand-in for the incus command: logs every call, answers `list` from $FAKE_DIR/list.json, fakes sha256sum and gpg.
# Knobs (environment): FAKE_RUNNING (non-empty: the app has a running process), FAKE_TARGET_HAS_DATA (the destination
# home is not empty), FAKE_COUNT_MISMATCH (the copy has fewer files than the source), FAKE_LDD_MISSING (a library
# the binary needs is missing), FAKE_FEED (JSON text served for a vendor update feed), FAKE_VERSION (dpkg version).
echo "$*" >> "$FAKE_DIR/calls.log"
case "$1" in
  list) cat "$FAKE_DIR/list.json" ;;
  init)
    # a created container shows up in the next `list`, owned by the caller ($FAKE_OWNER)
    name="$3"; grp="${name#appgroup-}"; [ "$name" = user-apps ] && grp=default
    sed -i "s/]\$/,{\"name\":\"$name\",\"status\":\"Running\",\"config\":{\"user.taos.kind\":\"app-group\",\"user.taos.group\":\"$grp\",\"user.taos.owner\":\"$FAKE_OWNER\"},\"devices\":{}}]/" "$FAKE_DIR/list.json" ;;
  profile) case "$2" in device) echo incusbr0 ;; esac ;;
  exec)
    ct="$2"
    case "$*" in
      *sha256sum*) echo "$FAKE_SHA  /var/tmp/x" ;;
      *--show-keys*) echo "fpr:::::::::$FAKE_FPR:" ;;
      *"/proc/"*environ*) [ -n "$FAKE_RUNNING" ] && echo "/proc/4242" ;;
      *"-printf"*) if [ -n "$FAKE_COUNT_MISMATCH" ] && [ "$ct" != "user-apps" ]; then echo "2 50"; else echo "3 100"; fi ;;
      *"-mindepth 1"*"-quit"*) [ -n "$FAKE_TARGET_HAS_DATA" ] && echo "x" ;;
      *apt-cache*policy*) printf '  Installed: %s\n  Candidate: %s\n' "${FAKE_VERSION:-1.0.0}" "${FAKE_CANDIDATE:-${FAKE_VERSION:-1.0.0}}" ;;
      *dpkg-query*) echo "${FAKE_VERSION_AFTER:-${FAKE_VERSION:-1.0.0}}" ;;
      *ldd*) [ -n "$FAKE_LDD_MISSING" ] && echo "libfoo.so.1 => not found" ;;
      *"curl -fsSL"*latest.json*) printf '%s' "$FAKE_FEED" ;;
      *"tar "*" -cf -"*) printf 'tardata' ;;
      *"tar "*" -xpf -"*) cat > /dev/null ;;
    esac ;;
esac
exit 0
