"""hub.taos.my node engine (own-your-posts P2P social network).

    See ``docs/design/hub-social-network-foundation.md``. This package holds the
    controller-side node engine: the identity keystore (slice 1), the object
    store + canonical-JSON/sign helpers (slice 2), and the follow / friend /
    circle relationship statements (slice 3). Later slices add the chain logic,
    sync workers, and peer server. Directory calls reach taos.my through
    ``tinyagentos/routes/account_proxy.py`` additions; the browser never sees the
    taos.my base URL.
    """
