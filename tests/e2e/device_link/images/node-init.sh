#!/bin/bash
# Container entrypoint for every bed node. It starts daemons only; joining the
# mesh happens later, from bed.sh, once it has minted keys (mesh-join.sh).
#
# Environment (set by compose):
#   NODE_NAME         the container's role name, e.g. a-host1
#   PERSONS           "id:slot ..." the people hosted here, e.g. "a:0" or "a:0 f:1".
#                     One userspace tailscaled per person (design 5.0 and 3.9).
#   STUBS             1 (default): bind a stand-in on each person's loopback ports
#                     27700+n and 27750+n. Set 0 once the real listeners exist.
#   CONTROLLER        1: also run the taOS controller (port 6969, data in /data).
#   PERSONAL_KERNEL   1: also run a kernel-tun tailscaled for a "personal tailnet"
#                     (default socket path /run/ts-personal.sock), standing in for
#                     the owner's own tailnet that the design says must stay untouched.
set -eu

LOG=/var/log/bed
mkdir -p "$LOG" /run/taos/mesh /var/lib/taos/mesh
# Sticky and world-writable: each person's daemon creates its own socket here.
chmod 1777 /run/taos/mesh

first_sock=""
for entry in ${PERSONS:-}; do
  p=${entry%%:*}
  n=${entry##*:}
  u="p_${p}"
  id -u "$u" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$u"
  state=/var/lib/taos/mesh/$p
  sock=/run/taos/mesh/$p.sock
  mkdir -p "$state"
  chown "$u:$u" "$state"
  chmod 700 "$state"
  [ -n "$first_sock" ] || first_sock=$sock

  # The flags are section 5.0's, verbatim in shape: userspace networking, own
  # statedir and socket, SOCKS5 at 27800+n. The HTTP proxy port (27850+n) is the
  # bed's own choice; the design leaves it open.
  setpriv --reuid="$u" --regid="$u" --init-groups \
    tailscaled --tun=userspace-networking --no-logs-no-support \
      --statedir="$state" --socket="$sock" \
      --socks5-server="localhost:$((27800 + n))" \
      --outbound-http-proxy-listen="localhost:$((27850 + n))" \
      >"$LOG/tailscaled-$p.log" 2>&1 &

  for _ in $(seq 1 50); do [ -S "$sock" ] && break; sleep 0.2; done
  # tailscaled makes its socket world-accessible; section 3.9 wants it owned by
  # the person's account, so close it to everyone else (SC5).
  [ -S "$sock" ] && chmod 600 "$sock"

  if [ "${STUBS:-1}" = 1 ]; then
    python3 /usr/local/bin/bed-stub.py "$((27700 + n))" "${NODE_NAME}:${p}:6977" >"$LOG/stub-$p-6977.log" 2>&1 &
    python3 /usr/local/bin/bed-stub.py "$((27750 + n))" "${NODE_NAME}:${p}:6969" >"$LOG/stub-$p-6969.log" 2>&1 &
  fi
done

if [ "${PERSONAL_KERNEL:-0}" = 1 ]; then
  # Default settings on purpose: kernel tun (tailscale0), default netfilter. This is
  # the "owner's own tailnet" the taOS instances must coexist with.
  tailscaled --no-logs-no-support --statedir=/var/lib/ts-personal --socket=/run/ts-personal.sock \
    >"$LOG/tailscaled-personal.log" 2>&1 &
fi

if [ "${CONTROLLER:-0}" = 1 ]; then
  mkdir -p /data /bed-presence
  : >/bed-presence/screen
  export TAOS_DATA_DIR=/data
  # The owner's entry, kept for compatibility (design 3.9a); the per-person
  # lookup is PR T9.
  [ -z "$first_sock" ] || export TAOS_TAILSCALE_SOCKET=$first_sock
  export TAOS_HEADSCALE_URL=${TAOS_HEADSCALE_URL:-http://headscale:8080}
  export TAOS_ACCOUNT_BASE_URL=${TAOS_ACCOUNT_BASE_URL:-http://taosmy:8080}
  export TAOS_DEVICE_LINK_TIME_SCALE=${TAOS_DEVICE_LINK_TIME_SCALE:-1}
  export TAOS_PRESENCE_SYSFS=/bed-presence/sysfs
  export TAOS_PRESENCE_SCREEN_FILE=/bed-presence/screen
  mkdir -p "$TAOS_PRESENCE_SYSFS"
  (cd /src && exec taos) >"$LOG/controller.log" 2>&1 &
fi

# A plain, signal-aware PID 1 child (compose runs with init: true).
exec sleep infinity
