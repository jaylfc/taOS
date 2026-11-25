#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# A stand-in for taos-appgroups for the Store's tests: logs its arguments to $FAKE_AG_DIR/args.log and answers from
# JSON files in $FAKE_AG_DIR (catalog.json, list.json, groups.json). FAKE_AG_FAIL makes install, move and update fail.
echo "$*" >> "$FAKE_AG_DIR/args.log"
case "$1" in
  catalog) cat "$FAKE_AG_DIR/catalog.json" ;;
  list) cat "$FAKE_AG_DIR/list.json" ;;
  groups) cat "$FAKE_AG_DIR/groups.json" ;;
  group) echo '{"event":"done","ok":true,"group":"made"}' ;;
  install|move|update)
    if [ "$2" = "--check" ] || [ "$3" = "--check" ]; then echo '{"event":"done","ok":true,"update_available":true,"latest_version":"2.0"}'; exit 0; fi
    echo '{"event":"step","n":1,"of":2,"text":"Working"}'
    if [ -n "$FAKE_AG_FAIL" ]; then echo '{"event":"done","ok":false,"code":7,"error":"Claude is running. Close it and try again; nothing was changed."}'; exit 7; fi
    echo '{"event":"done","ok":true}' ;;
esac
exit 0
