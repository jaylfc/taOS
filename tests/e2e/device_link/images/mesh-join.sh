#!/bin/bash
# Join one person's userspace instance to a control server and add the section
# 5.0 `serve` mappings. Run inside a node container by bed.sh.
#
#   mesh-join.sh PERSON SLOT HOSTNAME LOGIN_URL [--no-serve]   (auth key on stdin)
#
# The key arrives on stdin so it never appears in a process list.
set -eu
person=$1 slot=$2 host=$3 login=$4 noserve=${5:-}
sock=/run/taos/mesh/$person.sock
read -r key

# Every tailscale call passes --socket (design 5.0): without it you talk to the
# wrong daemon. --accept-dns=false on every join. No --accept-routes, no exit node.
ts() { tailscale --socket="$sock" "$@"; }

ts up --login-server="$login" --authkey="$key" --hostname="$host" \
  --accept-dns=false --timeout=60s

if [ "$noserve" != "--no-serve" ]; then
  # Inbound: a userspace node has no kernel listener, so each port needs an
  # explicit forward to that person's loopback port.
  ts serve --bg --tcp=6977 "tcp://127.0.0.1:$((27700 + slot))"
  ts serve --bg --tcp=6969 "tcp://127.0.0.1:$((27750 + slot))"
fi
ts ip -4
