"""taosctl video -- list and manage generated videos."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "video"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="List and manage generated videos")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List generated videos")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("generate", help="Generate a video from a prompt")
    gp.add_argument("prompt", help="Text prompt for the video")
    gp.add_argument("--model", default="wan2.1-1.3b", help="Model id")
    gp.add_argument("--duration", type=int, default=5, help="Duration in seconds")
    gp.add_argument("--resolution", default="480x832", help="Resolution WxH")
    gp.add_argument("--seed", type=int, default=None, help="Random seed")
    gp.set_defaults(func=_generate)

    dp = verbs.add_parser("delete", help="Delete a generated video")
    dp.add_argument("filename", help="Video filename")
    dp.set_defaults(func=_delete)


def _list(args, client):
    return client.get("/api/video")


def _generate(args, client):
    body = {
        "prompt": args.prompt,
        "model": args.model,
        "duration": args.duration,
        "resolution": args.resolution,
        "seed": args.seed,
    }
    return client.post("/api/video/generate", body=body)


def _delete(args, client):
    return client.delete(f"/api/video/{quote(args.filename, safe='')}")
