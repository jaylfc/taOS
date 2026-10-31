#!/usr/bin/env python3
"""Agent wall probe - runs inside agent container to report wall status."""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import ssl
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def http_get(
    url: str,
    timeout: float = 5.0,
    allow_http: bool = False,  # kept for compatibility
) -> Dict[str, Any]:
    """Perform a GET request. Returns dict with status code and outcome."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "status": None, "error": "invalid_scheme"}
    try:
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(
                parsed.hostname,
                parsed.port or 443,
                timeout=timeout,
                context=ctx,
            )
        else:
            conn = http.client.HTTPConnection(
                parsed.hostname,
                parsed.port or 80,
                timeout=timeout,
            )
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        conn.request("GET", path, headers={"User-Agent": "taos-agent-wall-probe/1.0"})
        resp = conn.getresponse()
        status = resp.status
        resp.read(1024)  # drain
        conn.close()
        return {"ok": True, "status": status, "error": None}
    except Exception as e:
        return {"ok": False, "status": None, "error": str(type(e).__name__)}


def tcp_connect(host: str, port: int, timeout: float = 3.0) -> Dict[str, Any]:
    """Try to connect TCP. Returns outcome."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "connected": True, "error": None}
    except socket.timeout:
        return {"ok": True, "connected": False, "error": "timeout"}
    except ConnectionRefusedError:
        return {"ok": True, "connected": False, "error": "refused"}
    except Exception as e:
        return {"ok": False, "connected": False, "error": str(type(e).__name__)}


def check_admin_api(controller: str) -> Dict[str, Any]:
    """admin-api: GET <controller>/api/cluster/map with no creds."""
    url = controller.rstrip("/") + "/api/cluster/map"
    res = http_get(url, timeout=5.0, allow_http=True)
    if not res["ok"]:
        return {
            "id": "admin-api",
            "status": "UNKNOWN",
            "detail": f"request_failed:{res['error']}",
        }
    status = res["status"]
    if status == 401 or status == 403:
        return {"id": "admin-api", "status": "PASS", "detail": f"http_{status}"}
    if status == 200:
        return {"id": "admin-api", "status": "FAIL", "detail": "http_200"}
    return {"id": "admin-api", "status": "UNKNOWN", "detail": f"http_{status}"}


def check_idmap(root: str = "/") -> Dict[str, Any]:
    """idmap: read /proc/self/uid_map."""
    path = Path(root) / "proc" / "self" / "uid_map"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {"id": "idmap", "status": "UNKNOWN", "detail": "unreadable"}
    try:
        for line in content.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            container_uid = int(parts[0])
            host_uid = int(parts[1])
            if container_uid == 0:
                if host_uid < 100000:
                    return {
                        "id": "idmap",
                        "status": "FAIL",
                        "detail": f"root_maps_to_{host_uid}",
                    }
                else:
                    return {
                        "id": "idmap",
                        "status": "PASS",
                        "detail": f"root_maps_to_{host_uid}",
                    }
        return {"id": "idmap", "status": "PASS", "detail": "no_root_mapping_found"}
    except Exception:
        return {"id": "idmap", "status": "UNKNOWN", "detail": "parse_error"}


def check_devices(root: str = "/") -> Dict[str, Any]:
    """devices: FAIL if any restricted device exists."""
    devices = [
        "dev/dri",
        "dev/kfd",
        "dev/nvidia0",
        "dev/rknpu",
        "dev/mali0",
    ]
    found = []
    for dev in devices:
        p = Path(root) / dev
        try:
            if p.exists() or p.is_symlink():
                if dev.startswith("dev/"):
                    found.append(dev[4:])  # dev/ is 4 chars
                else:
                    found.append(dev)
        except Exception:
            pass
    if found:
        return {
            "id": "devices",
            "status": "FAIL",
            "detail": "found:" + ",".join(sorted(found)),
        }
    return {"id": "devices", "status": "PASS", "detail": "none"}


def check_controller_data(root: str = "/") -> Dict[str, Any]:
    """controller-data: FAIL if /opt/taos or /opt/tinyagentos exists and is listable."""
    candidates = ["opt/taos", "opt/tinyagentos"]
    found = []
    for c in candidates:
        p = Path(root) / c
        try:
            if p.exists():
                try:
                    # listable check - exists and is listable
                    list(p.iterdir())
                    found.append(c)
                except Exception:
                    # exists but not listable
                    pass
        except Exception:
            pass
    if found:
        return {
            "id": "controller-data",
            "status": "FAIL",
            "detail": "found:" + ",".join(sorted(found)),
        }
    return {"id": "controller-data", "status": "PASS", "detail": "none"}


def check_limits(root: str = "/") -> Dict[str, Any]:
    """limits: read memory.max and pids.max from cgroup."""
    paths = [
        Path(root) / "sys" / "fs" / "cgroup" / "memory.max",
        Path(root) / "sys" / "fs" / "cgroup" / "pids.max",
    ]
    values = []
    for p in paths:
        try:
            values.append(p.read_text(encoding="utf-8", errors="replace").strip())
        except Exception:
            return {"id": "limits", "status": "UNKNOWN", "detail": "unreadable"}
    # Check both are numbers (not 'max')
    try:
        for v in values:
            if v == "max":
                return {"id": "limits", "status": "FAIL", "detail": "unlimited"}
            # must be parseable as int
            int(v)
        return {"id": "limits", "status": "PASS", "detail": "bounded"}
    except Exception:
        return {"id": "limits", "status": "UNKNOWN", "detail": "parse_error"}


def check_reach_target(host: str, port: int, timeout: float = 3.0) -> Dict[str, Any]:
    """reach:<host:port>"""
    res = tcp_connect(host, port, timeout=timeout)
    if not res["ok"]:
        return {
            "id": f"reach:{host}:{port}",
            "status": "UNKNOWN",
            "detail": f"error:{res['error']}",
        }
    if res["connected"]:
        return {"id": f"reach:{host}:{port}", "status": "FAIL", "detail": "connected"}
    # refused or timeout - both mean not connected / blocked/walled appropriately
    return {"id": f"reach:{host}:{port}", "status": "PASS", "detail": res["error"]}


def check_internet_control(url: str) -> Dict[str, Any]:
    """internet-control: HTTPS GET"""
    res = http_get(url, timeout=10.0)
    if not res["ok"] or res["status"] is None:
        return {
            "id": "internet-control",
            "status": "UNKNOWN",
            "detail": f"failed:{res['error']}",
        }
    # any response is fine as positive control
    return {
        "id": "internet-control",
        "status": "PASS",
        "detail": f"http_{res['status']}",
    }


def check_share_acl() -> Dict[str, Any]:
    """share-acl: always SKIP"""
    return {
        "id": "share-acl",
        "status": "SKIP",
        "detail": "no project shares yet (S5)",
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent wall probe")
    parser.add_argument("--controller", default=None)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--internet", default="https://example.com")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def compute_exit_code(results: List[Dict[str, Any]]) -> int:
    has_fail = any(r["status"] == "FAIL" for r in results)
    has_unknown = any(r["status"] == "UNKNOWN" for r in results)
    if has_fail:
        return 1
    if has_unknown:
        return 2
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    controller = args.controller or os.environ.get("TAOS_URL") or "http://127.0.0.1:6969"

    results: List[Dict[str, Any]] = []

    # Run checks in order
    results.append(check_admin_api(controller))
    results.append(check_idmap("/"))
    results.append(check_devices("/"))
    results.append(check_controller_data("/"))
    results.append(check_limits("/"))

    # internet control first
    internet_res = check_internet_control(args.internet)
    results.append(internet_res)

    # parse targets
    targets = []
    for t in args.target:
        # handle IPv6 [::1]:80
        if t.startswith("["):
            try:
                end = t.find("]:")
                if end != -1:
                    host = t[1:end]
                    port_str = t[end + 2 :]
                    port = int(port_str)
                    targets.append((host, port))
                    continue
            except Exception:
                pass
        if ":" in t:
            try:
                host, port_str = t.rsplit(":", 1)
                port = int(port_str)
                targets.append((host, port))
            except Exception:
                pass

    internet_ok = internet_res["status"] == "PASS"
    for host, port in targets:
        res = check_reach_target(host, port)
        if not internet_ok and res["status"] == "PASS":
            # turn PASS into UNKNOWN if internet control failed
            results.append(
                {
                    "id": res["id"],
                    "status": "UNKNOWN",
                    "detail": res["detail"],
                }
            )
        else:
            results.append(res)

    results.append(check_share_acl())

    if args.json:
        print(json.dumps(results))
    else:
        for r in results:
            print(f"{r['status']} {r['id']} {r['detail']}")

    return compute_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
