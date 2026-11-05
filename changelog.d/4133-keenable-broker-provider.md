### Added

- Secrets Broker: Keenable (web search + page fetch) joins the curated provider registry, keyed by `KEENABLE_API_KEY` with `X-Api-Key` header auth, plus a thin `broker/clients/keenable.py` client that falls back to Keenable's keyless public endpoints when no key is set.
