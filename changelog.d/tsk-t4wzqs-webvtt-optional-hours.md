### Fixed

- WebVTT captions emitted with hours-less timestamps (`MM:SS.mmm`, the form YouTube caption tools and other editors produce for sub-hour media) now parse. The old parser required `HH:MM:SS.mmm` on every timestamp and silently indexed such files as "no captions".
- HTML entities in cue text (e.g. `&#39;`, `&amp;`) are now unescaped before indexing, so apostrophes and ampersands survive into the transcript instead of their raw entity form.
- A caption blob that is not a WebVTT file (no `WEBVTT` header) now makes the fetcher log a warning instead of returning an empty transcript reported as success; `parse_vtt` raises `ValueError` for non-empty non-WebVTT input.
- `download_video()` now reads its output path from yt-dlp's `--print after_move:filepath` machine-readable route instead of scraping the human-readable `[download] Destination:` stdout line.
