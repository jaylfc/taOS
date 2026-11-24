#!/bin/sh
# A stand-in for Chromium in tests. Records its command line and environment, then behaves like a browser that owns
# its profile: it creates the SingletonLock symlink (host-pid) and stays "open" until the file $FAKE_BROWSER_STOP
# appears (or 20 s pass). With --version it prints a version.
for a in "$@"; do
  [ "$a" = "--version" ] && { echo "Fake Chromium 1.2.3"; exit 0; }
done
profile=""
for a in "$@"; do
  case "$a" in --user-data-dir=*) profile=${a#--user-data-dir=};; esac
done
log=${FAKE_BROWSER_LOG:-/dev/null}
{ echo "ARGS:$*"; echo "ENV_TEST:${TAOS_FAKE_PIN:-}"; echo "ENV_LD:${LD_LIBRARY_PATH:-}"; echo "ENV_QT:${QT_PLUGIN_PATH:-}${QML_IMPORT_PATH:-}"; echo "ENV_GPU:${MESA_LOADER_DRIVER_OVERRIDE:-}"; } >> "$log"
if [ -n "$profile" ]; then
  mkdir -p "$profile"
  ln -sfn "fakehost-$$" "$profile/SingletonLock"
  i=0
  while [ ! -e "${FAKE_BROWSER_STOP:-/nonexistent}" ] && [ $i -lt 200 ]; do sleep 0.1; i=$((i+1)); done
  rm -f "$profile/SingletonLock"
fi
exit 0
