### Fixed

- AbortSignal.timeout polyfill now throws TypeError for negative, non-finite, or too-large delays and chains timers for delays beyond setTimeout's 2147483647 ms limit.
- /api/models/recommended now validates tier variant IDs against model.variants; models with invalid variant IDs are omitted.
- check_manifests.py summary now labels warning count as "model capability field warnings" instead of "model manifests missing capability fields".