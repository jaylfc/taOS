#!/usr/bin/env python3
"""The foundation's fake controller, extended for the Store (stdlib only).

The shapes are those the real routes return (tinyagentos/routes/store.py at origin/dev):
  GET  /api/store/catalog                 list of entries {id,name,type,category,version,description,icon,license,
                                          weights_license,license_class,requires,hardware_tiers,install_method,
                                          installed,state,variants,repo,stars,popularity,update_available,upstream_*}
  GET  /api/store/installed-v2            {"installed":[{app_id,installed_at,version,metadata,runtime_host,...}]}
  GET  /api/store/app/<id>                the detail
  GET  /api/hardware                      {profile_id,...}
  GET  /api/models                        {models:[{id,name,variants:[{id,compatibility,downloaded,size_mb}],compatibility}],...}
  POST /api/store/install-v2              admin only (403), 412 needs_license_acceptance, 404, 500 per control
  POST /api/store/uninstall               admin only
  GET  /api/store/install-progress/by-app/<id>
  GET  /auth/me                           is_admin follows the `admin` control
Controls (POST /__fake/config): {"store": "seed"}, {"admin": bool}, {"outcome": {"<app>": {"status":..,"body":..}}},
{"install_delay_ms": N}, {"catalog_fail": 403}. GET /__fake/calls -> [{"path","body"}].
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "testing"))
import fake_controller as fc  # noqa: E402

TIERS_ALL = {"cpu-only": "full", "x86-cuda-12gb": "full"}


def entry(id, name, type="service", category="", version="1.0.0", description="", install_method="", installed=False,
          requires=None, tiers=None, variants=None, license="MIT", weights_license="", license_class="", stars=0,
          runtime=None, update=False, state=None):
    d = dict(id=id, name=name, type=type, category=category, version=version, description=description, icon="",
             license=license, weights_license=weights_license, license_class=license_class,
             requires=requires or {}, hardware_tiers=tiers if tiers is not None else dict(TIERS_ALL),
             install_method=install_method, installed=installed, state=state or ("installed" if installed else "not_installed"),
             variants=variants or [], repo="", stars=stars, popularity={"github_stars": stars, "installs": None, "score": 0.0},
             update_available=update, upstream_version=None, upstream_pinned_version=None,
             upstream_update_available=None, upstream_checked_at=None)
    if runtime:
        d["runtime"] = runtime
    return d


def seed():
    fc.state["admin"] = False
    fc.state["store_calls"] = []
    fc.state["outcome"] = {}
    fc.state["install_delay_ms"] = 0
    fc.state["catalog_fail"] = 0
    fc.state["progress"] = {}
    fc.state["installed_ids"] = {"uptime-kuma": "1.0.0", "smolagents": "1.0.0"}
    fc.state["accepted"] = set()
    fc.state["catalog"] = [
        entry("searxng", "SearXNG", category="infrastructure", version="2026.10.2", description="Privacy-respecting metasearch engine",
              install_method="docker", requires={"ram_mb": 128, "disk_mb": 200}, stars=15000),
        entry("gitea", "Gitea", category="dev-tool", description="Lightweight self-hosted Git service", install_method="lxc",
              requires={"ram_mb": 512, "disk_mb": 1024}, stars=45000),
        entry("n8n", "n8n", category="automation", description="Workflow automation platform", install_method="docker",
              runtime="either", requires={"ram_mb": 512, "disk_mb": 2048}, stars=90000),
        entry("uptime-kuma", "Uptime Kuma", category="monitoring", description="A fancy self-hosted monitoring tool",
              install_method="docker", installed=True, update=True, version="1.1.0", stars=60000),
        entry("musicgen", "MusicGen", category="audio", description="Text to music", install_method="script",
              license_class="non-commercial", weights_license="CC-BY-NC 4.0", license="MIT"),
        entry("openclaw", "OpenClaw", type="agent-framework", description="Full-featured agent with multi-channel support", install_method="pip"),
        entry("smolagents", "SmolAgents", type="agent-framework", description="Code-based agents", install_method="pip", installed=True),
        entry("web-search", "Web Search", type="plugin", description="Search the web via SearXNG", install_method="pip"),
        entry("mcp-memory", "MCP Memory", type="mcp", description="Knowledge graph memory", install_method="pip"),
        entry("qwen3-8b", "Qwen3 8B", type="model", description="Most capable local model for 16GB devices", install_method="download",
              tiers={"cpu-only": "limited"},
              variants=[{"id": "q4", "name": "Q4", "size_mb": 4800, "backend": ["llama-cpp"]}, {"id": "q8", "name": "Q8", "size_mb": 8600, "backend": ["llama-cpp"]}]),
        entry("gemma-3-1b", "Gemma 3 1B", type="model", description="Small and quick", install_method="download",
              variants=[{"id": "q4", "name": "Q4", "size_mb": 800, "backend": ["llama-cpp"]}]),
        entry("flux-dev-gguf", "Flux Dev", type="model", description="Image model", install_method="download", tiers={"cpu-only": "unsupported"},
              variants=[{"id": "q4", "name": "Q4", "size_mb": 12000, "backend": ["sd-cpp"]}]),
    ]
    fc.state["models"] = [
        {"id": "qwen3-8b", "name": "Qwen3 8B", "compatibility": "yellow", "has_downloaded_variant": False,
         "variants": [{"id": "q4", "compatibility": "yellow", "downloaded": False, "size_mb": 4800},
                      {"id": "q8", "compatibility": "red", "downloaded": False, "size_mb": 8600}]},
        {"id": "gemma-3-1b", "name": "Gemma 3 1B", "compatibility": "green", "has_downloaded_variant": False,
         "variants": [{"id": "q4", "compatibility": "green", "downloaded": False, "size_mb": 800}]},
        {"id": "flux-dev-gguf", "name": "Flux Dev", "compatibility": "red", "has_downloaded_variant": False,
         "variants": [{"id": "q4", "compatibility": "red", "downloaded": False, "size_mb": 12000}]},
    ]


def listed():
    out = []
    for e in fc.state["catalog"]:
        e = dict(e)
        if e["id"] in fc.state["installed_ids"]:
            e["installed"] = True
            e["state"] = "installed"
        out.append(e)
    return out


class H(fc.H):
    def record(self, path, body=None):
        fc.state.setdefault("store_calls", []).append(dict(path=path, body=body))

    def r_GET(self, path, q):
        st = fc.state
        if path == "/__fake/calls":
            return self.send_json(st.get("store_calls", []))
        if path == "/auth/me" and self.headers.get("Authorization", "") == "":
            return self.send_json({"user": {"id": "usr_1", "username": "alex", "is_admin": bool(st.get("admin"))}})
        if path == "/api/store/catalog":
            if st.get("catalog_fail"):
                return self.send_json({"detail": "forbidden"}, st["catalog_fail"])
            return self.send_json(listed())
        if path == "/api/store/installed-v2":
            rows = [dict(app_id=i, installed_at=1760000000.0, version=v, metadata={}, runtime_host="127.0.0.1" if i == "uptime-kuma" else None,
                         runtime_port=3001 if i == "uptime-kuma" else None, runtime_backend="docker" if i == "uptime-kuma" else None)
                    for i, v in st["installed_ids"].items()]
            return self.send_json({"installed": rows})
        if path == "/api/hardware":
            return self.send_json({"profile_id": "cpu-only", "recommended_framework": "smolagents"})
        if path == "/api/models":
            return self.send_json({"models": st["models"], "downloaded_files": [], "hardware_profile_id": "cpu-only"})
        parts = path.strip("/").split("/")
        if parts[:3] == ["api", "store", "app"] and len(parts) == 4:
            e = next((x for x in st["catalog"] if x["id"] == parts[3]), None)
            if e is None:
                return self.send_json({"error": "App '%s' not found" % parts[3]}, 404)
            return self.send_json(dict(id=e["id"], name=e["name"], type=e["type"], version=e["version"], description=e["description"],
                                       homepage="https://example.org/" + e["id"], license=e["license"], requires=e["requires"],
                                       install={"method": e["install_method"]}, hardware_tiers=e["hardware_tiers"],
                                       config_schema=[{"name": "port"}] if e["id"] == "gitea" else [], variants=e["variants"],
                                       capabilities=["search"] if e["id"] == "searxng" else [], installed=e["id"] in st["installed_ids"],
                                       state="installed" if e["id"] in st["installed_ids"] else "not_installed"))
        if parts[:4] == ["api", "store", "install-progress", "by-app"] and len(parts) == 5:
            a = st["progress"].get(parts[4])
            return self.send_json({"app_id": parts[4], "active": a})
        return super().r_GET(path, q)

    def r_POST(self, path, q):
        st = fc.state
        if path == "/api/store/install-v2":
            b = self.body()
            self.record(path, b)
            app = b.get("app_id")
            if not st.get("admin"):
                return self.send_json({"detail": "Admin access required"}, 403)
            out = st["outcome"].get(app)
            e = next((x for x in st["catalog"] if x["id"] == app), None)
            if out:
                return self.send_json(out.get("body", {}), out.get("status", 200))
            if e is None:
                return self.send_json({"error": "app_id required"}, 400)
            if e["license_class"] == "non-commercial" and not b.get("accepted") and app not in st["accepted"]:
                return self.send_json({"needs_license_acceptance": True, "license_id": e["weights_license"], "weights_license": e["weights_license"],
                                       "name": e["name"], "text": "x"}, 412)
            if b.get("accepted"):
                st["accepted"].add(app)
            st["progress"][app] = dict(install_id="i1", app_id=app, state="downloading", percent=40.0, bytes_downloaded=400, bytes_total=1000,
                                       detail="downloading", error=None)
            time.sleep(st.get("install_delay_ms", 0) / 1000.0)
            st["installed_ids"][app] = e["version"]
            st["progress"][app] = dict(st["progress"][app], state="installed", percent=100.0)
            return self.send_json({"ok": True, "app_id": app, "status": "installed"})
        if path == "/api/store/uninstall":
            b = self.body()
            self.record(path, b)
            if not st.get("admin"):
                return self.send_json({"detail": "Admin access required"}, 403)
            if b.get("app_id") not in st["installed_ids"]:
                return self.send_json({"error": "App '%s' not installed" % b.get("app_id")}, 404)
            del st["installed_ids"][b["app_id"]]
            return self.send_json({"status": "ok"})
        return super().r_POST(path, q)


_orig_control = fc.H.control


def control(self, verb, path):
    if path == "/__fake/calls":
        return self.send_json(fc.state.get("store_calls", []))
    body = self.body()
    with fc.lock:
        if body.get("store") == "seed":
            seed()
        for k in ("admin", "outcome", "install_delay_ms", "catalog_fail"):
            if k in body:
                fc.state[k] = body[k]
    return _orig_control(self, verb, path)


H.control = control

_orig_fx = fc.fx


def fx(name):
    raw = _orig_fx(name)
    if name == "auth_login.json":
        d = json.loads(raw)
        d["user"]["is_admin"] = bool(fc.state.get("admin"))
        return json.dumps(d)
    return raw


fc.fx = fx


def main():
    import argparse
    from http.server import ThreadingHTTPServer
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    fc.reset()
    seed()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    print("LISTENING %d" % srv.server_address[1], flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
