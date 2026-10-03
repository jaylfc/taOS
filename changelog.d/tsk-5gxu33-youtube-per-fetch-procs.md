### Fixed

- YouTube fetcher: each fetch and video download now drops a yt-dlp subprocess from its own tracker as soon as it exits, so cleanup on cancel or timeout only kills that call's still-running processes and never signals an exited one. The leftover no-op `_cleanup_procs()` module hook and its call in the Library pipeline's timeout path are removed; the pipeline's timeout cancels the fetch, whose own cleanup handles it. (tsk-5gxu33)
