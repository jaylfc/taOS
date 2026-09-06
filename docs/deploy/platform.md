# DEPLOYMENT, tinyagentos.com platform LXC

Step-by-step runbook for provisioning the platform LXC and bringing up the
public-facing tinyagentos.com website, docs, and opentracker.

---

## 0. Prereqs on the Proxmox host

Before starting, confirm all of the following on the Proxmox host.

**a. Debian 12 standard template is downloaded.**

```bash
pveam list local | grep debian-12-standard
```

Expected output: one line containing `debian-12-standard_12.*_amd64.tar.zst`.

If missing:
```bash
pveam update
pveam download local debian-12-standard
```

Wait for the download to finish (`pveam list local` shows it when done).

**b. Know your Proxmox bridge name.**

```bash
ip link show | grep vmbr
```

Default is `vmbr0`. If yours differs, set `CT_BRIDGE=<your-bridge>` in step 2.

**c. Know your storage pool name.**

```bash
pvesm status
```

Look for the pool you want to store the rootfs on. Default assumed: `local-lvm`.
If yours differs, set `CT_STORAGE=<pool-name>` in step 2.

**d. Decide on a static IP (recommended) or DHCP.**

A static IP makes DNS setup predictable. Confirm an available address on your
LAN and note the gateway IP.

**e. Repo is accessible on the Proxmox host.**

Either clone the repo or ensure `scripts/install-platform-lxc.sh` and its siblings
are present on the host filesystem:

```bash
git clone --depth 1 https://github.com/jaylfc/tinyagentos /opt/tinyagentos
```

---

## 1. DNS records to create

Create these A records at your DNS provider (names.co.uk) before or alongside
provisioning. TLS cert issuance will fail until DNS points at the LXC.

Replace `<LXC-IP>` with the actual IPv4 address you will assign or receive via
DHCP.

| Type | Name                     | Value      | TTL  |
|------|--------------------------|------------|------|
| A    | tinyagentos.com          | `<LXC-IP>` | 300  |
| A    | www.tinyagentos.com      | `<LXC-IP>` | 300  |
| A    | docs.tinyagentos.com     | `<LXC-IP>` | 300  |
| A    | tracker.tinyagentos.com  | `<LXC-IP>` | 300  |

For IPv6, add matching AAAA records once you know the container's IPv6 address.

Set TTL to 300 (5 minutes) initially so you can iterate quickly. Raise to 3600
after everything is confirmed working.

---

## 2. Run install-lxc.sh on the Proxmox host

```bash
cd /opt/tinyagentos

# Example: static IP 192.168.1.50/24, gateway 192.168.1.1, default bridge + storage
sudo CTID=200 CT_IP=192.168.1.50/24 CT_GW=192.168.1.1 bash scripts/install-platform-lxc.sh
```

Or with DHCP:
```bash
sudo CTID=200 bash scripts/install-platform-lxc.sh
```

Available env vars:

| Variable        | Default                                  | Purpose                        |
|-----------------|------------------------------------------|--------------------------------|
| `CTID`          | next free ID via `pvesh`                 | Proxmox container ID           |
| `CT_IP`         | `dhcp`                                   | Static IP in CIDR, or `dhcp`   |
| `CT_GW`         | (required if CT_IP is static)            | Gateway IP                     |
| `CT_BRIDGE`     | `vmbr0`                                  | Proxmox network bridge         |
| `CT_STORAGE`    | `local-lvm`                              | Proxmox storage pool           |
| `CT_DISK_DATA`  | `0` (no data disk)                       | Size in GB for `/var/mail` disk |
| `CT_TEMPLATE`   | auto-detected Debian 12 template         | Override template path         |

Expected terminal output on success (last few lines):
```
[install-lxc] platform LXC provisioning complete
  Container ID : 200
  IP address   : 192.168.1.50
  Hostname     : tinyagentos-platform
  ...
```

If the script exits with an error, read the message carefully. Common issues
are covered in section 7 (Known issues).

---

## 3. Verify the container is up and reachable

```bash
# Container status on the Proxmox host
pct status <CTID>
# Expected: status: running

# SSH into the container
ssh root@<LXC-IP>

# Confirm services
systemctl is-active caddy postgresql opentracker fail2ban
# Expected: four lines each reading "active"

# TUN device
test -c /dev/net/tun && echo "tun: ok" || echo "tun: MISSING"

# Postgres connection
psql -U tinyagentos -c '\conninfo'
# Expected: something like "You are connected to database "tinyagentos" ..."
```

---

## 4. DNS propagation check

Before Caddy can issue TLS certificates, DNS must resolve to the LXC.

```bash
# From outside your LAN (or using a public resolver):
dig +short tinyagentos.com @1.1.1.1
dig +short www.tinyagentos.com @1.1.1.1
dig +short docs.tinyagentos.com @1.1.1.1
dig +short tracker.tinyagentos.com @1.1.1.1
```

Each should return `<LXC-IP>`. If they do not, wait for TTL to expire and
check the DNS provider's control panel. TTL=300 means at most 5 minutes.

---

## 5. TLS certificate issuance

Caddy handles cert issuance automatically. Once DNS resolves to the LXC, make
the first HTTP request to each domain. Caddy will complete the ACME HTTP-01
challenge and cache the certificate.

```bash
# Trigger cert issuance for each domain
curl -I http://tinyagentos.com
curl -I http://docs.tinyagentos.com
curl -I http://tracker.tinyagentos.com
```

On the first request Caddy will redirect HTTP to HTTPS and perform the ACME
challenge in the background. Wait 10-20 seconds, then:

```bash
curl -I https://tinyagentos.com
# Expected: HTTP/2 200 (or 301 from www redirect)

curl -I https://docs.tinyagentos.com
# Expected: HTTP/2 200

curl -I https://tracker.tinyagentos.com
# Expected: HTTP/2 200 (proxied to opentracker)
```

If certs are not issued after 60 seconds, check Caddy logs inside the
container:

```bash
ssh root@<LXC-IP>
journalctl -u caddy --no-pager -n 50
# Also check:
tail -n 50 /var/log/caddy/error.log
```

Common causes: DNS not yet propagated, port 80 not reachable from the internet
(check firewall/NAT rules on the Proxmox host and upstream router).

---

## 6. Smoke tests per subdomain

Run all of these and confirm expected output before marking provisioning done.

```bash
LXC=<LXC-IP>

# Landing page returns valid HTML with correct title
curl -sf https://tinyagentos.com | grep -c '<title>TinyAgentOS'
# Expected: 1

# www redirects to apex
curl -sI https://www.tinyagentos.com | head -3
# Expected: HTTP/2 301 then Location: https://tinyagentos.com/

# Docs placeholder or built site loads
curl -sf https://docs.tinyagentos.com | grep -c 'html'
# Expected: 1 or more

# opentracker announces endpoint responds
curl -s "https://tracker.tinyagentos.com/announce" | head -c 40
# Expected: some BitTorrent tracker response (not a 502)

# Security headers present
curl -sI https://tinyagentos.com | grep -i strict-transport
# Expected: strict-transport-security: max-age=63072000 ...
```

---

## 7. Known issues and rollback

### Container create fails: "template not found"

Download the template first:
```bash
pveam update && pveam download local debian-12-standard
```

### opentracker build fails

The build depends on `libowfat`. If the vendored libowfat build fails due to
a missing tool, install `cvs` and `build-essential` manually inside the
container and re-run `provision.sh` after removing the sentinel:

```bash
pct exec <CTID> -- apt-get install -y cvs build-essential
pct exec <CTID> -- rm /var/lib/tinyagentos-platform/provisioned
pct exec <CTID> -- bash /root/provision.sh
```

### Caddy fails to validate Caddyfile

Run `caddy validate --config /etc/caddy/Caddyfile` inside the container to
see the exact error. Most common cause: a site block referencing a directory
that does not exist yet. Check `/var/www/` ownership.

### Full rollback

To destroy the container and start over:

```bash
pct stop <CTID>
pct destroy <CTID>
```

Then re-run `install-lxc.sh` with the same or a new CTID.

DNS records can stay in place across a rollback, the IP address should not
change if you use a static assignment.

---

## 8. What to tackle after phase 1

Once the above is confirmed working, these are the natural next steps:

| Ref | Task |
|-----|------|
| #91 | Add GitHub Actions workflow to auto-deploy docs on push to `master` |
| #91 | Build MkDocs site and deploy: `cd site/docs && mkdocs build && rsync ...` |
| #92 | Verify opentracker IPv6 announce endpoint and UDP announce (see issue #92 acceptance criteria) |
| #92 | Add AAAA DNS records for IPv6 |
|  | Set up `restic` backup job for Postgres and `/var/www` |
|  | Raise DNS TTL to 3600 after confirming everything works |
|  | Configure fail2ban email alerts (optional; requires mail setup) |
|  | Add Grafana panel showing opentracker announces/minute (deferred from #92) |
|  | Integrate Headscale: TUN pass-through is already enabled in the LXC config |
