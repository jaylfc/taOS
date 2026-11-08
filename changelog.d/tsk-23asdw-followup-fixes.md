### Fixed

- AbortSignal.timeout polyfill now throws TypeError for negative, non-finite, or too-large delays; long delays are split into setTimeout-sized steps and abort at the requested time.
- /api/models/recommended now validates tier variant IDs against model.variants; models with invalid variant IDs are omitted.
- check_manifests.py summary now labels warning count as "model capability field warnings" instead of "model manifests missing capability fields".