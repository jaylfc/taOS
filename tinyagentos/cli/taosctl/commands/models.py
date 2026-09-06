"""taosctl models -- inspect and manage models."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "models"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage models")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all models")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one model by id")
    gp.add_argument("id", help="Model id")
    gp.set_defaults(func=_get)

    dp = verbs.add_parser("delete", help="Delete a model")
    dp.add_argument("id", help="Model id")
    dp.set_defaults(func=_delete)

    rp = verbs.add_parser("recommended", help="List recommended models")
    rp.set_defaults(func=_recommended)

    ldp = verbs.add_parser("loaded", help="List loaded models")
    ldp.set_defaults(func=_loaded)

    dlp = verbs.add_parser("downloads", help="List downloads")
    dlp.set_defaults(func=_downloads)

    dwp = verbs.add_parser("download", help="Start a model download")
    dwp.add_argument("--app-id", required=True, help="App id")
    dwp.add_argument("--variant-id", required=True, help="Variant id")
    dwp.set_defaults(func=_download)

    pp = verbs.add_parser("pull", help="Pull an Ollama model")
    pp.add_argument("--model-name", required=True, help="Model name")
    pp.set_defaults(func=_pull)

    # SKIP: search (needs query params + multi-source fan-out)
    # SKIP: files/{model_id} (path with slashes, needs special encoding)
    # SKIP: downloads/{download_id} (single-resource sub-path)


def _list(args, client):
    return client.get("/api/models")


def _get(args, client):
    return client.get(f"/api/models/{quote(args.id, safe='')}")


def _delete(args, client):
    return client.delete(f"/api/models/{quote(args.id, safe='')}")


def _recommended(args, client):
    return client.get("/api/models/recommended")


def _loaded(args, client):
    return client.get("/api/models/loaded")


def _downloads(args, client):
    return client.get("/api/models/downloads")


def _download(args, client):
    body = {"app_id": args.app_id, "variant_id": args.variant_id}
    return client.post("/api/models/download", body=body)


def _pull(args, client):
    body = {"model_name": args.model_name}
    return client.post("/api/models/pull", body=body)
