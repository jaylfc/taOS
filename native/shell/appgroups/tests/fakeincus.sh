#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# A stand-in for the incus command: logs every call, answers `list` from $FAKE_DIR/list.json, fakes sha256sum and gpg.
echo "$*" >> "$FAKE_DIR/calls.log"
case "$1" in
  list) cat "$FAKE_DIR/list.json" ;;
  profile) case "$2" in device) echo incusbr0 ;; esac ;;
  exec)
    case "$*" in
      *sha256sum*) echo "$FAKE_SHA  /var/tmp/x" ;;
      *--show-keys*) echo "fpr:::::::::$FAKE_FPR:" ;;
    esac ;;
esac
exit 0
