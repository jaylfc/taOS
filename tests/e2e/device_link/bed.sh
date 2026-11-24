#!/usr/bin/env bash
# The device-link test bed (T0). Test-only and opt-in; see README.md.
#
#   bed.sh up        build and start everything, mint keys, seed, join the nodes
#   bed.sh down      remove every container, network and volume of the bed
#   bed.sh status    containers, and the node tables of both Headscales
#   bed.sh exec NODE CMD...   run a command inside a node (what the tests use)
#   bed.sh hs ARGS...         the taOS Headscale CLI
#   bed.sh hsp ARGS...        the personal-tailnet Headscale CLI
#   bed.sh my ARGS...         the taos.my operator CLI (python -m server.opcli)
#   bed.sh policy repo|allow-all   swap the taOS Headscale policy (MT5) and reload it
#   bed.sh stop|start SERVICE...   stop or start services (taos.my down tests)
#   bed.sh nodes     print .state/nodes.json (the table the tests read)
#
# Sources (never copied into this repo; exported to .state/ with git archive):
#   TAOS_SRC_DIR   a directory holding the taOS tree to test. Default: an export
#                  (git archive) of TAOS_REF, default HEAD, of the repo this script
#                  lives in. Uncommitted changes are not included.
#   TAOS_MY_DIR    a directory holding the taos.my tree. Or set TAOS_MY_REPO (a
#                  clone of the taos.my repo) and optionally TAOS_MY_REF
#                  (default origin/main).
# Options: TAOS_BED_STUBS=0|1 (default 1), BED_TIME_SCALE (default 60).
#
# Nothing here needs a secret and nothing connects outside the machine running
# docker: every key is minted at run time inside the bed.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STATE=$HERE/.state
PROJECT=device-link-bed
cd "$HERE"

die() { echo "bed.sh: $*" >&2; exit 1; }

# person id, account email, display name. Headscale users are taos-<account_id>
# exactly as taos.my names them (server/headscale.py user_name_for_account).
PEOPLE=(a b f)
email_of() { echo "person-$1@bed.test"; }

# node : person : slot : headscale hostname. shared-s hosts two people, one
# daemon each, under their own Headscale users and handles.
JOINS=(
  "a-host1:a:0:a-host1"
  "a-host2:a:0:a-host2"
  "a-host3:a:0:a-host3"
  "a-phone:a:0:a-phone"
  "b-host:b:0:b-host"
  "b-phone:b:0:b-phone"
  "shared-s:a:0:shared-s-a"
  "shared-s:f:1:shared-s-f"
  "f-phone:f:0:f-phone"
)
PERSONAL_NODES=(personal a-host1 a-phone)

compose() {
  docker compose -p "$PROJECT" -f "$HERE/compose.core.yml" -f "$HERE/compose.nodes.yml" "$@"
}
hs()  { compose exec -T headscale headscale "$@"; }
hsp() { compose exec -T headscale-personal headscale "$@"; }
my()  { compose exec -T taosmy python -m server.opcli "$@"; }
pyj() { python3 -c "$@"; }

export_ref() { # REPO REF DEST
  rm -rf "$3"; mkdir -p "$3"
  git -C "$1" archive "$2" | tar -x -C "$3"
}

resolve_sources() {
  mkdir -p "$STATE"
  if [ -z "${TAOS_SRC_DIR:-}" ]; then
    # Always an export of a committed ref (default HEAD), never the working tree: the
    # tree holds .state/ (minted keys), which must not be baked into the node images.
    repo=$(git -C "$HERE" rev-parse --show-toplevel)
    export_ref "$repo" "${TAOS_REF:-HEAD}" "$STATE/taos-src"
    TAOS_SRC_DIR=$STATE/taos-src
  fi
  if [ -z "${TAOS_MY_DIR:-}" ]; then
    [ -n "${TAOS_MY_REPO:-}" ] || die "set TAOS_MY_DIR (a taos.my tree) or TAOS_MY_REPO (a taos.my clone)"
    export_ref "$TAOS_MY_REPO" "${TAOS_MY_REF:-origin/main}" "$STATE/taos-my-src"
    TAOS_MY_DIR=$STATE/taos-my-src
  fi
  [ -f "$TAOS_MY_DIR/deploy/headscale/policy.hujson" ] || die "TAOS_MY_DIR has no deploy/headscale/policy.hujson"
  [ -f "$TAOS_MY_DIR/Dockerfile" ] || die "TAOS_MY_DIR has no Dockerfile"
  export TAOS_SRC_DIR TAOS_MY_DIR BED_DIR=$HERE
}

placeholder_sources() { # commands that never build still need the variables to parse
  export TAOS_SRC_DIR=${TAOS_SRC_DIR:-/nonexistent} TAOS_MY_DIR=${TAOS_MY_DIR:-/nonexistent} BED_DIR=$HERE
  [ -f "$STATE/hs.env" ] && { set -a; . "$STATE/hs.env"; set +a; } || true
}

wait_for() { # DESCRIPTION SECONDS CMD...
  local what=$1 secs=$2; shift 2
  local i
  for ((i = 0; i < secs; i++)); do
    "$@" >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "timed out after ${secs}s waiting for: $what"
}

hs_user_id() { # name
  hs users list -o json | pyj "import json,sys; print(next(u['id'] for u in json.load(sys.stdin) if u['name']==sys.argv[1]))" "$1"
}
hsp_user_id() {
  hsp users list -o json | pyj "import json,sys; print(next(u['id'] for u in json.load(sys.stdin) if u['name']==sys.argv[1]))" "$1"
}

# Reusable, NON-ephemeral, 24h keys: roster devices are never ephemeral (design 4.0).
mint_key() { # headscale-fn user-id [extra args]
  local fn=$1 uid=$2; shift 2
  "$fn" preauthkeys create --user "$uid" --reusable --expiration 24h "$@" | tail -n 1 | tr -d '\r'
}

# The address prefixes are appended at run time, assembled from parts, so the committed
# configs carry no address literals (the same approach taos.my's entrypoint takes).
render_headscale_configs() {
  local hi=100 ula=fd7a
  for n in config personal; do
    {
      cat "$HERE/headscale/$n.yaml"
      printf '\nprefixes:\n  v4: %s.64.0.0/10\n  v6: %s:115c:a1e0::/48\n  allocation: sequential\n' "$hi" "$ula"
    } >"$STATE/headscale-$n.yaml"
  done
}

cmd_up() {
  [ ! -f "$STATE/up" ] || die "already up (or half up); run 'bed.sh down' first"
  command -v docker >/dev/null || die "docker is required"
  resolve_sources
  : >"$STATE/nodes.tsv"
  touch "$STATE/up"

  # Runtime copies of the policy: the repo's (from the taos.my tree) and an allow-all
  # for MT5's deliberate misconfiguration. Written under .state/, never committed.
  cat "$TAOS_MY_DIR/deploy/headscale/policy.hujson" >"$STATE/policy.repo.hujson"
  cat >"$STATE/policy.allow-all.hujson" <<'JSON'
{
  "tagOwners": {"tag:relay": ["taos-relay@"]},
  "acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]
}
JSON
  cat "$STATE/policy.repo.hujson" >"$STATE/policy.hujson"
  render_headscale_configs

  echo "== control plane"
  compose up -d --build headscale headscale-personal
  wait_for "headscale" 120 hs users list
  wait_for "personal headscale" 120 hsp users list

  # An API key for taos.my, minted here, kept 0600 in .state, never committed.
  key=$(hs apikeys create --expiration 24h | tail -n 1 | tr -d '\r')
  (umask 077; printf 'HEADSCALE_API_KEY=%s\n' "$key" >"$STATE/hs.env")
  set -a; . "$STATE/hs.env"; set +a

  echo "== taos.my"
  compose up -d --build taosmy
  wait_for "taos.my /healthz" 180 compose exec -T taosmy python3 -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/healthz',timeout=3)"

  echo "== seed accounts (operator CLI: user create, entitlement grant; no mail, no Stripe)"
  : >"$STATE/accounts.tsv"
  for p in "${PEOPLE[@]}"; do
    out=$(my user create --email "$(email_of "$p")" --name "Person ${p^^}" --verified --apply)
    uid=$(printf '%s' "$out" | pyj "import json,sys; print(json.load(sys.stdin)['user_id'])")
    my entitlement grant "$(email_of "$p")" --apply >/dev/null
    printf '%s\t%s\t%s\n' "$p" "$uid" "$(email_of "$p")" >>"$STATE/accounts.tsv"
    hs users create "taos-$uid" >/dev/null
  done
  hs users create taos-relay >/dev/null

  echo "== nodes"
  compose up -d --build a-host1 a-host2 a-host3 a-phone b-host b-phone shared-s f-phone relay personal
  for j in "${JOINS[@]}"; do
    IFS=: read -r node person slot host <<<"$j"
    wait_for "$node socket for $person" 60 compose exec -T "$node" test -S "/run/taos/mesh/$person.sock"
  done
  wait_for "relay socket" 60 compose exec -T relay test -S /run/taos/mesh/relay.sock

  for j in "${JOINS[@]}"; do
    IFS=: read -r node person slot host <<<"$j"
    uid=$(awk -F'\t' -v p="$person" '$1==p{print $2}' "$STATE/accounts.tsv")
    huid=$(hs_user_id "taos-$uid")
    jkey=$(mint_key hs "$huid")
    ip=$(printf '%s\n' "$jkey" | compose exec -T "$node" mesh-join.sh "$person" "$slot" "$host" http://headscale:8080 | tail -n 1 | tr -d '\r')
    printf '%s\t%s\t%s\t%s\t%s\ttaos-%s\t%s\n' "$node" "$person" "$slot" "$host" "$uid" "$uid" "$ip" >>"$STATE/nodes.tsv"
    echo "joined $host ($node, person $person) $ip"
  done

  # The relay: Headscale user taos-relay, tagged tag:relay (the repo policy lets that
  # tag reach every node on 6969 only). No serve mappings.
  rid=$(hs_user_id taos-relay)
  rkey=$(mint_key hs "$rid" --tags tag:relay)
  rip=$(printf '%s\n' "$rkey" | compose exec -T relay mesh-join.sh relay 49 taos-relay http://headscale:8080 --no-serve | tail -n 1 | tr -d '\r')
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' relay relay 49 taos-relay - taos-relay "$rip" >>"$STATE/nodes.tsv"
  echo "joined taos-relay $rip"

  echo "== personal-tailnet stand-in (kernel tun, second Headscale)"
  hsp users create personal >/dev/null
  puid=$(hsp_user_id personal)
  pkey=$(mint_key hsp "$puid")
  : >"$STATE/personal.tsv"
  for node in "${PERSONAL_NODES[@]}"; do
    compose exec -T "$node" sh -c 'for i in $(seq 1 50); do [ -S /run/ts-personal.sock ] && exit 0; sleep 0.2; done; exit 1'
    compose exec -T "$node" tailscale --socket=/run/ts-personal.sock up \
      --login-server=http://headscale-personal:8080 --authkey="$pkey" \
      --hostname="$node-personal" --accept-dns=false --timeout=60s
    pip=$(compose exec -T "$node" tailscale --socket=/run/ts-personal.sock ip -4 | tail -n 1 | tr -d '\r')
    printf '%s\t%s\n' "$node" "$pip" >>"$STATE/personal.tsv"
    echo "personal tailnet: $node $pip"
  done

  python3 - "$STATE" <<'PY'
import csv, json, sys
state = sys.argv[1]
nodes = []
for row in csv.reader(open(f"{state}/nodes.tsv"), delimiter="\t"):
    node, person, slot, host, acct, hsuser, ip = row
    nodes.append(dict(node=node, person=person, slot=int(slot), hostname=host,
                      account_id=acct, headscale_user=hsuser, ip=ip))
accounts = {r[0]: dict(account_id=r[1], email=r[2])
            for r in csv.reader(open(f"{state}/accounts.tsv"), delimiter="\t")}
personal = {r[0]: r[1] for r in csv.reader(open(f"{state}/personal.tsv"), delimiter="\t")}
json.dump(dict(nodes=nodes, accounts=accounts, personal=personal), open(f"{state}/nodes.json", "w"), indent=2)
PY
  touch "$STATE/ready"
  echo "== bed is up. Run: TAOS_E2E_DEVICE_LINK=1 pytest tests/e2e/device_link -v"
}

cmd_down() {
  placeholder_sources
  compose down -v --remove-orphans || true
  rm -rf "$STATE"
}

cmd_policy() { # repo | allow-all : swap the taOS Headscale policy and reload it
  case "${1:-}" in repo|allow-all) ;; *) die "usage: bed.sh policy repo|allow-all" ;; esac
  placeholder_sources
  cat "$STATE/policy.$1.hujson" >"$STATE/policy.hujson"   # same inode: the bind mount follows
  compose kill -s HUP headscale
  sleep 3
}

cmd_status() {
  placeholder_sources
  compose ps || true
  echo "-- taOS headscale nodes"; hs nodes list || true
  echo "-- personal headscale nodes"; hsp nodes list || true
  [ -f "$STATE/ready" ] && echo "bed: ready" || echo "bed: NOT ready"
}

sub=${1:-}
[ $# -gt 0 ] && shift
case "$sub" in
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  exec) placeholder_sources; node=$1; shift; compose exec -T "$node" "$@" ;;
  hs) placeholder_sources; hs "$@" ;;
  hsp) placeholder_sources; hsp "$@" ;;
  my) placeholder_sources; my "$@" ;;
  policy) cmd_policy "$@" ;;
  stop|start) placeholder_sources; compose "$sub" "$@" ;;
  nodes) cat "$STATE/nodes.json" ;;
  *) sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
