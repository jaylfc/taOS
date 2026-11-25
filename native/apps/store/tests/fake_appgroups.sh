#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# A stand-in for taos-appgroups for the Store's tests: logs its arguments to $FAKE_AG_DIR/args.log and answers from
# JSON files in $FAKE_AG_DIR (catalog.json, list.json, groups.json). FAKE_AG_FAIL makes install, move and update fail.
echo "$*" >> "$FAKE_AG_DIR/args.log"
# the person's confirmation the Store handed over (environment, never an argument): kept per command for the tests
if [ -n "$TAOS_OWNER_NONCE" ]; then echo "$TAOS_OWNER_NONCE" > "$FAKE_AG_DIR/nonce-$1"; else rm -f "$FAKE_AG_DIR/nonce-$1"; fi
case "$1" in
  catalog) cat "$FAKE_AG_DIR/catalog.json" ;;
  list) cat "$FAKE_AG_DIR/list.json" ;;
  groups) cat "$FAKE_AG_DIR/groups.json" ;;
  licence) echo '{"event":"done","ok":true}' ;;
  group) [ -n "$FAKE_AG_SLOW" ] && sleep 1; echo '{"event":"done","ok":true,"group":"made"}' ;;
  install|move|update)
    if [ "$2" = "--check" ] || [ "$3" = "--check" ]; then
      if [ -n "$FAKE_AG_UNVERIFIED" ]; then echo '{"event":"done","ok":true,"update_available":true,"latest_version":"2.0","verification":{"checksum_pinned_in_catalogue":false,"signed_source":false,"sha256":"abcd","summary":"NOT verified: the checksum comes from the same feed as the download."}}'; exit 0; fi
      echo '{"event":"done","ok":true,"update_available":true,"latest_version":"2.0"}'; exit 0; fi
    echo '{"event":"step","n":1,"of":2,"text":"Working"}'
    if [ -n "$FAKE_AG_FAIL" ]; then echo '{"event":"done","ok":false,"code":7,"error":"Claude is running. Close it and try again; nothing was changed."}'; exit 7; fi
    echo '{"event":"done","ok":true}' ;;
esac
exit 0
