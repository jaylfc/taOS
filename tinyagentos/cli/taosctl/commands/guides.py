"""taosctl guides -- query hardware guides and model recommendations."""
from __future__ import annotations

NOUN = "guides"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Query hardware guides and model recommendations")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    rp = verbs.add_parser("recommendations", help="Model recommendations for a hardware tier and use case")
    rp.add_argument("hardware", help="Hardware tier, e.g. pi-16gb, nvidia-12gb, cpu-only")
    rp.add_argument("use_case", help="Use case, e.g. chat, coding, embedding, vision, voice")
    rp.set_defaults(func=_recommendations)

    tp = verbs.add_parser("tiers", help="List hardware tiers with labels and descriptions")
    tp.set_defaults(func=_tiers)

    up = verbs.add_parser("use-cases", help="List use cases with labels and descriptions")
    up.set_defaults(func=_use_cases)


def _recommendations(args, client):
    return client.get(
        "/api/guides/recommendations",
        params={"hardware": args.hardware, "use_case": args.use_case},
    )


def _tiers(args, client):
    return client.get("/api/guides/tiers")


def _use_cases(args, client):
    return client.get("/api/guides/use-cases")
