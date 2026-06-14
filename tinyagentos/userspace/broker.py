from __future__ import annotations

import httpx
from pathlib import Path

# Headers an app may NOT set on a backend-proxy call — these would let it
# spoof identity/routing or exfiltrate the session.
_BLOCKED_PROXY_HEADERS = {"host", "authorization", "cookie",
                          "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"}

# Capability namespaces granted to every app without consent.
FREE_CAPS = {"app.kv", "app.table", "app.files", "app.notify", "app.window"}
# Capability namespaces that require an explicit granted permission.
GATED_CAPS = {"app.net", "app.agent", "app.llm", "app.memory"}


def _namespace(capability: str) -> str:
    parts = capability.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else capability


async def handle_capability(app_id, capability, args, *, granted, data_store, app_dir, services):
    """Dispatch one capability call. Returns {"result": ...} or {"error": ...}.

    Enforces: gated caps require membership in `granted`; data calls are
    namespaced by app_id; file calls are jailed to app_dir/files.
    """
    ns = _namespace(capability)
    if ns not in FREE_CAPS and ns not in GATED_CAPS:
        return {"error": "unknown_capability", "capability": capability}
    if ns in GATED_CAPS and ns not in granted:
        return {"error": "permission_denied", "capability": capability}

    args = args or {}

    if capability == "app.kv.get":
        return {"result": await data_store.kv_get(app_id, args["key"])}
    if capability == "app.kv.set":
        await data_store.kv_set(app_id, args["key"], args.get("value")); return {"result": True}
    if capability == "app.kv.delete":
        await data_store.kv_delete(app_id, args["key"]); return {"result": True}
    if capability == "app.kv.keys":
        return {"result": await data_store.kv_keys(app_id)}
    if capability == "app.table.insert":
        return {"result": await data_store.table_insert(app_id, args["table"], args.get("row", {}))}
    if capability == "app.table.query":
        return {"result": await data_store.table_query(app_id, args["table"], args.get("where"))}
    if capability == "app.table.delete":
        await data_store.table_delete(app_id, args["table"], args["id"]); return {"result": True}

    if capability in ("app.files.read", "app.files.write"):
        files_root = (Path(app_dir) / "files").resolve()
        target = (files_root / args.get("path", "")).resolve()
        if not str(target).startswith(str(files_root) + "/") and target != files_root:
            return {"error": "invalid_path"}
        if capability == "app.files.read":
            if not target.is_file():
                return {"error": "not_found"}
            return {"result": target.read_text()}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args.get("content", "")); return {"result": True}

    if capability == "app.notify":
        notif = services.get("notifications")
        if notif is not None:
            await notif.add_notification({
                "source": app_id, "title": args.get("title", app_id),
                "body": args.get("body", ""), "level": "info", "icon": "layout-grid"})
        return {"result": True}
    if capability == "app.window":
        return {"result": True}  # window ops handled client-side; no-op server result

    # Gated caps (only reached when granted)
    if capability == "app.memory.search":
        mem = services.get("memory")
        _search = getattr(mem, "search", None) if mem is not None else None
        if _search is None:
            return {"result": []}
        try:
            return {"result": await _search(args.get("q", ""))}
        except TypeError:
            return {"result": []}
    if capability == "app.agent":
        agent = services.get("agent")
        return {"result": await agent.ask(args.get("name"), args.get("message")) if agent else None}
    if capability == "app.llm":
        llm = services.get("llm")
        return {"result": await llm.complete(args.get("prompt", "")) if llm else None}
    if capability == "app.net":
        base = services.get("app_backend_url")
        if not base:
            return {"error": "no_backend"}
        path = str(args.get("path", "/"))
        if "://" in path or path.startswith("//") or ".." in path.split("/"):
            return {"error": "invalid_path"}
        url = base.rstrip("/") + "/" + path.lstrip("/")
        method = str(args.get("method", "GET")).upper()
        _raw = args.get("headers") or {}
        _headers = {k: v for k, v in _raw.items()
                    if k.lower() not in _BLOCKED_PROXY_HEADERS} or None
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
                resp = await c.request(
                    method, url,
                    json=args.get("body") if args.get("body") is not None else None,
                    headers=_headers,
                )
            return {"result": {"status": resp.status_code, "body": resp.text}}
        except httpx.HTTPError as exc:
            return {"error": "backend_unreachable", "detail": str(exc)}

    return {"error": "unknown_capability", "capability": capability}
