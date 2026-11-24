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

# The bed's Headscale serves its embedded DERP over plain http (no TLS in the
# bed), and the DERP map carries no InsecureForTests flag, so without this every
# client dials DERP with TLS, fails, and reports "could not connect to the
# bed-embedded relay server". Direct paths still work between warmed peers, but a
# first contact waits on DERP and can outlast a test's timeout.
export TS_DEBUG_USE_DERP_HTTP=1

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
  [ -n "$first_sock" ] || first_sock=$sock

  if [ "$p" = relay ]; then
    # The relay stand-in is the "net" image: no taOS code in it, so its daemon
    # stays hand-rolled (slot 49, outside the allocator's reach; "relay" is a
    # reserved person id there). Flags are section 5.0's.
    mkdir -p "$state"
    chown "$u:$u" "$state"
    chmod 700 "$state"
    setpriv --reuid="$u" --regid="$u" --init-groups \
      tailscaled --tun=userspace-networking --no-logs-no-support \
        --statedir="$state" --socket="$sock" \
        --socks5-server="localhost:$((27800 + n))" \
        --outbound-http-proxy-listen="localhost:$((27850 + n))" \
        >"$LOG/tailscaled-$p.log" 2>&1 &
    for _ in $(seq 1 50); do [ -S "$sock" ] && break; sleep 0.2; done
    [ -S "$sock" ] && chmod 600 "$sock"
  else
    # Every other node runs the product code: the slot comes from the allocator
    # (as the superuser here; the product's allocator runs as its service
    # account) and the daemon is launched by `mesh_instances exec` as the
    # person's own account, the bed's model of the per-person Linux accounts
    # the design migrates to. State and run roots are the module's defaults.
    got=$(python3 -m tinyagentos.taosnet.mesh_instances allocate "$p" |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["slot"])')
    if [ "$got" != "$n" ]; then
      echo "node-init: FAIL: allocator gave person $p slot $got, the bed declares $n" >&2
      exit 1
    fi
    chown -R "$u:$u" "$state"
    chmod 700 "$state"
    setpriv --reuid="$u" --regid="$u" --init-groups \
      python3 -m tinyagentos.taosnet.mesh_instances exec "$p" \
      >"$LOG/tailscaled-$p.log" 2>&1 &
    # `exec` closes the socket to 0600 itself; fail loudly if it never does.
    ok=""
    for _ in $(seq 1 100); do
      [ -S "$sock" ] && [ "$(stat -c %a "$sock")" = 600 ] && { ok=1; break; }
      sleep 0.2
    done
    if [ -z "$ok" ]; then
      echo "node-init: FAIL: $sock never appeared with mode 600 (see $LOG/tailscaled-$p.log)" >&2
      exit 1
    fi
  fi

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
