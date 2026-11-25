#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# A stand-in for the incus command: logs every call, answers `list` from $FAKE_DIR/list.json, fakes sha256sum and gpg.
# Knobs (environment): FAKE_RUNNING (non-empty: the app has a running process), FAKE_TARGET_HAS_DATA (the destination
# home is not empty), FAKE_COUNT_MISMATCH (the copy has fewer files than the source), FAKE_LDD_MISSING (a library
# the binary needs is missing), FAKE_FEED (JSON text served for a vendor update feed), FAKE_VERSION (dpkg version).
echo "$*" >> "$FAKE_DIR/calls.log"
# FAKE_ETAG_FAILS=<n>: the first n `config device` calls fail like a concurrent edit does; FAKE_SERIALIZE_CHECK: two
# `config device` calls at the same time conflict (and leave a note in $FAKE_DIR/conflicts)
if [ "$1" = config ] && [ "$2" = device ]; then
  if [ -n "$FAKE_ETAG_FAILS" ]; then
    n=$(cat "$FAKE_DIR/etag.count" 2>/dev/null || echo 0)
    if [ "$n" -lt "$FAKE_ETAG_FAILS" ]; then echo $((n+1)) > "$FAKE_DIR/etag.count"; echo "Error: Failed to update device: ETag doesn't match" >&2; exit 1; fi
  fi
  if [ -n "$FAKE_SERIALIZE_CHECK" ]; then
    if mkdir "$FAKE_DIR/busy" 2>/dev/null; then sleep 0.5; rmdir "$FAKE_DIR/busy"
    else echo conflict >> "$FAKE_DIR/conflicts"; echo "Error: Failed to update device: ETag doesn't match" >&2; exit 1; fi
  fi
fi
case "$1" in
  list) cat "$FAKE_DIR/list.json" ;;
  file)
    # `incus file pull <ct>/<path> <local>`: a PNG-looking file (the icon pulled out of the container)
    if [ "$2" = pull ]; then printf '\211PNG\r\n\032\nfake-%s' "$3" > "$4"; fi ;;
  init)
    # a created container shows up in the next `list`, owned by the caller ($FAKE_OWNER)
    name="$3"; grp="${name#appgroup-}"; [ "$name" = user-apps ] && grp=default
    sed -i "s/]\$/,{\"name\":\"$name\",\"status\":\"Running\",\"config\":{\"user.taos.kind\":\"app-group\",\"user.taos.group\":\"$grp\",\"user.taos.owner\":\"$FAKE_OWNER\"},\"devices\":{}}]/" "$FAKE_DIR/list.json" ;;
  profile) case "$2" in device) echo incusbr0 ;; esac ;;
  exec)
    ct="$2"
    case "$*" in
      *content-sum*) h=aaaa; [ -n "$FAKE_CONTENT_MISMATCH" ] && [ "$ct" != "user-apps" ] && h=bbbb
                     if [ -n "$FAKE_COUNT_MISMATCH" ] && [ "$ct" != "user-apps" ]; then echo "2 50 $h"; else echo "3 100 $h"; fi ;;
      *sha256sum*) echo "$FAKE_SHA  /var/tmp/x" ;;
      *--show-keys*)
        # a key listing: one primary key by default. FAKE_KEY_EXTRA adds a second primary key, FAKE_KEY_SUBONLY puts the
        # expected fingerprint on a subkey of some other primary key, FAKE_KEY_SWAP_AFTER_INSTALL makes the key stored
        # on disk (the /etc/apt/keyrings file) a different one from what was downloaded.
        case "$*" in *keyrings*) [ -n "$FAKE_STORED_FPR" ] && FAKE_FPR="$FAKE_STORED_FPR" ;; esac
        if [ -n "$FAKE_KEY_SUBONLY" ]; then echo "pub:-:4096:1:1111111111111111:1700000000:::-:::scESC::::::23::0:"; echo "fpr:::::::::0000000000000000000000000000000000000000:"; echo "sub:-:4096:1:2222222222222222:1700000000::::::s::::::23:"; echo "fpr:::::::::$FAKE_FPR:"
        else echo "pub:-:4096:1:1111111111111111:1700000000:::-:::scESC::::::23::0:"; echo "fpr:::::::::$FAKE_FPR:"; echo "sub:-:4096:1:3333333333333333:1700000000::::::e::::::23:"; echo "fpr:::::::::5555555555555555555555555555555555555555:"
          [ -n "$FAKE_KEY_EXTRA" ] && { echo "pub:-:4096:1:4444444444444444:1700000000:::-:::scESC::::::23::0:"; echo "fpr:::::::::9999999999999999999999999999999999999999:"; }
        fi ;;
      *"/proc/"*environ*) [ -n "$FAKE_RUNNING" ] && echo "/proc/4242" ;;
      *"-printf"*) if [ -n "$FAKE_COUNT_MISMATCH" ] && [ "$ct" != "user-apps" ]; then echo "2 50"; else echo "3 100"; fi ;;
      # a bare `find <home>` on a home that does not exist yet prints an error on the merged output (seen on a real container)
      *"-- find "*"-mindepth 1"*"-quit"*) echo "find: '/home/taos/apps/x': No such file or directory" ;;
      *"-mindepth 1"*"-quit"*) [ -n "$FAKE_TARGET_HAS_DATA" ] && echo "x" ;;
      *apt-cache*policy*) printf '  Installed: %s\n  Candidate: %s\n' "${FAKE_VERSION:-1.0.0}" "${FAKE_CANDIDATE:-${FAKE_VERSION:-1.0.0}}" ;;
      *"Icon="*) [ -z "$FAKE_NO_ICON" ] && echo "/usr/share/icons/hicolor/256x256/apps/found.png" ;;
      *dpkg-query*) echo "${FAKE_VERSION_AFTER:-${FAKE_VERSION:-1.0.0}}" ;;
      *ldd*) [ -n "$FAKE_LDD_MISSING" ] && echo "libfoo.so.1 => not found" ;;
      *"curl -fsSL"*latest.json*) printf '%s' "$FAKE_FEED" ;;
      *"tar "*" -cf -"*) printf 'tardata' ;;
      *"tar "*" -xpf -"*) cat > /dev/null ;;
    esac ;;
esac
exit 0
