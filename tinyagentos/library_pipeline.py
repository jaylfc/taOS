"""Library ingest pipeline — processors for cheap-tier file/text/pdf/image ingestion.

Processors are registered per detected kind and run asynchronously after ingest.
Each processor produces artifacts (e.g. metadata, extracted text, thumbnails)
that are stored on the item and optionally handed off to taosmd collections.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import time
from pathlib import Path

from tinyagentos.library_store import LibraryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kind detection
# ---------------------------------------------------------------------------

_MIME_KIND_MAP: dict[str, str] = {
    "text/plain": "text",
    "text/markdown": "text",
    "text/csv": "text",
    "text/html": "text",
    "application/json": "text",
    "application/xml": "text",
    "text/xml": "text",
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/svg+xml": "image",
    "application/zip": "archive",
    "application/gzip": "archive",
    "application/x-tar": "archive",
}


def detect_kind(source_url: str = "", content_type: str = "",
                file_path: str = "") -> str:
    """Detect the library item kind from URL, MIME, or file path."""
    # URL-based detection
    if source_url:
        lower = source_url.lower()
        if any(lower.startswith(p) for p in ("https://www.youtube.com/",
                                              "https://youtube.com/",
                                              "https://youtu.be/",
                                              "https://m.youtube.com/")):
            return "url:youtube"
        from tinyagentos.routes.lora_studio import is_civitai_url
        if is_civitai_url(source_url):
            return "url:civitai"
        if any(lower.startswith(p) for p in ("https://", "http://")):
            return "url:web"

    # MIME-based detection
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in _MIME_KIND_MAP:
            return _MIME_KIND_MAP[ct]

    # File extension fallback
    if file_path:
        ext = Path(file_path).suffix.lower()
        ext_map = {
            ".txt": "text", ".md": "text", ".csv": "text",
            ".json": "text", ".xml": "text", ".html": "text",
            ".pdf": "pdf",
            ".png": "image", ".jpg": "image", ".jpeg": "image",
            ".gif": "image", ".webp": "image", ".svg": "image",
            ".zip": "archive", ".gz": "archive", ".tar": "archive",
        }
        if ext in ext_map:
            return ext_map[ext]

    return "file"


# ---------------------------------------------------------------------------
# Processor registry
# ---------------------------------------------------------------------------

class Processor:
    """Base processor. Subclasses handle one kind of library item."""

    def __init__(self, store: LibraryStore, storage_dir: Path):
        self.store = store
        self.storage_dir = storage_dir

    async def process(self, item: dict) -> list[dict]:
        """Run processing on an item, return list of artifact dicts produced."""
        raise NotImplementedError


class FileProcessor(Processor):
    """Generic file processor — records basic metadata only."""

    async def process(self, item: dict) -> list[dict]:
        item_id = item["id"]
        artifacts: list[dict] = []

        storage_path = item.get("storage_path", "")
        source_url = item.get("source_url", "")
        if storage_path:
            p = Path(storage_path)
            if p.exists():
                stat = p.stat()
                file_meta = {
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "source_url": source_url,
                    "processed_at": time.time(),
                    "processor": "FileProcessor/v1",
                }
                # Try mimetype detection
                mime_type, _ = mimetypes.guess_type(p.name)
                if mime_type:
                    file_meta["mime_type"] = mime_type

                # path="" because storage_path is the user's original uploaded
                # file — the reprocess unlink loop must never delete it.
                await self.store.add_artifact(
                    item_id, kind="metadata", path="", meta=file_meta
                )
                artifacts.append({"kind": "metadata", "path": "", "meta": file_meta})

                # Update item bytes
                await self.store.update_item(item_id, bytes=stat.st_size)

        return artifacts


class TextProcessor(Processor):
    """Text file processor — extracts content as text artifact."""

    async def process(self, item: dict) -> list[dict]:
        item_id = item["id"]
        artifacts: list[dict] = []

        storage_path = item.get("storage_path", "")
        if not storage_path:
            return artifacts

        p = Path(storage_path)
        if not p.exists():
            logger.warning("Text processor: file not found %s", storage_path)
            return artifacts

        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            logger.warning("Text processor: could not read %s", storage_path,
                           exc_info=True)
            return artifacts

        # Write extracted text as an artifact
        text_dir = self.storage_dir / "text"
        text_dir.mkdir(parents=True, exist_ok=True)
        text_path = text_dir / f"{item_id}.txt"
        text_path.write_text(text, encoding="utf-8")

        text_meta = {
            "char_count": len(text),
            "line_count": text.count("\n") + 1,
            "source_url": item.get("source_url", ""),
            "processed_at": time.time(),
            "processor": "TextProcessor/v1",
        }
        await self.store.add_artifact(
            item_id, kind="text", path=str(text_path), meta=text_meta
        )
        artifacts.append({"kind": "text", "path": str(text_path), "meta": text_meta})

        # Store a preview (first 200 chars)
        preview = text[:200]
        meta = json.loads(item.get("meta_json", "{}"))
        meta["preview"] = preview
        await self.store.update_item(item_id, meta_json=meta)

        # Auto-title from content if no title
        if not item.get("title"):
            title = text.strip().split("\n", 1)[0][:100]
            if title:
                await self.store.update_item(item_id, title=title)

        return artifacts


class PdfProcessor(Processor):
    """PDF processor — extracts page count and OCR-ready text (when available)."""

    async def process(self, item: dict) -> list[dict]:
        item_id = item["id"]
        artifacts: list[dict] = []

        storage_path = item.get("storage_path", "")
        if not storage_path:
            return artifacts

        p = Path(storage_path)
        if not p.exists():
            return artifacts

        pdf_meta = {"page_count": 0, "has_text": False}

        # Try extracting text with PyPDF2 / pypdf if available
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            pdf_meta["page_count"] = len(reader.pages)

            # Extract text from all pages
            pages_text: list[str] = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    pages_text.append(page_text)

            if pages_text:
                text_content = "\n\n".join(pages_text)
                pdf_meta["has_text"] = True
                pdf_meta["char_count"] = len(text_content)

                text_dir = self.storage_dir / "text"
                text_dir.mkdir(parents=True, exist_ok=True)
                text_path = text_dir / f"{item_id}_pdf.txt"
                text_path.write_text(text_content, encoding="utf-8")

                await self.store.add_artifact(
                    item_id, kind="text", path=str(text_path),
                    meta={"char_count": len(text_content), "pages": len(reader.pages)},
                )
                artifacts.append({
                    "kind": "text", "path": str(text_path),
                    "meta": {"char_count": len(text_content), "pages": len(reader.pages)},
                })

                # Update item with preview
                preview = text_content[:200]
                meta = json.loads(item.get("meta_json", "{}"))
                meta["preview"] = preview
                await self.store.update_item(item_id, meta_json=meta)
        except ImportError:
            logger.debug("pypdf not installed — PDF text extraction skipped")
        except Exception:
            logger.warning("PDF text extraction failed for %s", storage_path,
                           exc_info=True)

        await self.store.add_artifact(
            item_id, kind="metadata", path="", meta=pdf_meta
        )
        artifacts.append({"kind": "metadata", "path": "", "meta": pdf_meta})

        return artifacts


class ImageProcessor(Processor):
    """Image processor — records dimensions, creates thumbnail.

    Thumbnail generation requires Pillow (PIL), which is always available in
    the taOS dev dependencies.
    """

    async def process(self, item: dict) -> list[dict]:
        item_id = item["id"]
        artifacts: list[dict] = []

        storage_path = item.get("storage_path", "")
        if not storage_path:
            return artifacts

        p = Path(storage_path)
        if not p.exists():
            return artifacts

        img_meta: dict = {"width": 0, "height": 0, "format": ""}

        try:
            from PIL import Image
            with Image.open(p) as img:
                img_meta["width"] = img.width
                img_meta["height"] = img.height
                img_meta["format"] = img.format or ""

                # Create thumbnail (max 320px on longest side)
                thumb_dir = self.storage_dir / "thumbs"
                thumb_dir.mkdir(parents=True, exist_ok=True)
                thumb_path = thumb_dir / f"{item_id}_thumb.jpg"

                img.thumbnail((320, 320))
                # Convert to RGB if needed (e.g. RGBA/PNG → JPEG)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(thumb_path, "JPEG", quality=75)

                img_meta["thumbnail"] = str(thumb_path)
                await self.store.add_artifact(
                    item_id, kind="thumbnail", path=str(thumb_path),
                    meta={"width": img.width, "height": img.height},
                )
                artifacts.append({
                    "kind": "thumbnail", "path": str(thumb_path),
                    "meta": {"width": img.width, "height": img.height},
                })
        except ImportError:
            logger.debug("PIL not available — image processing skipped")
        except Exception:
            logger.warning("Image processing failed for %s", storage_path,
                           exc_info=True)

        await self.store.add_artifact(
            item_id, kind="metadata", path="", meta=img_meta
        )
        artifacts.append({"kind": "metadata", "path": "", "meta": img_meta})

        return artifacts


class YouTubeProcessor(Processor):
    """YouTube URL processor — cheap tier: metadata, thumbnail, transcript, chapters.

    Uses yt-dlp via the knowledge_fetchers.youtube module to fetch video
    metadata and captions without downloading the video file. Produces
    artifacts that flow into taosmd collections for agent querying.

    The cheap tier (per the design doc, docs/design/library-app.md section 4)
    covers steps 1-4: canonical link, title, channel, description, thumbnail,
    duration, upload date, subtitles/transcript, chapters.
    """

    _YTDLP_TIMEOUT = 120  # seconds

    async def process(self, item: dict) -> list[dict]:
        item_id = item["id"]
        source_url = item.get("source_url", "")
        artifacts: list[dict] = []

        if not source_url:
            return artifacts

        # Only catch ImportError (missing yt-dlp).  Let fetch errors
        # propagate so run_pipeline marks the item as "error" — a failed
        # yt-dlp invocation must not silently look successful.
        from tinyagentos.knowledge_fetchers.youtube import (
            fetch,
            format_timestamp,
            _cleanup_procs,
        )

        media_dir = self.storage_dir / "youtube"
        try:
            result = await asyncio.wait_for(
                fetch(source_url, media_dir=media_dir),
                timeout=self._YTDLP_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _cleanup_procs()
            raise

        title = result.get("title", "")
        if title and not item.get("title"):
            await self.store.update_item(item_id, title=title)

        meta = result.get("metadata", {})

        # Update item meta_json with structured video metadata
        stored_meta = json.loads(item.get("meta_json", "{}"))
        stored_meta.update({
            "video_id": meta.get("video_id", ""),
            "channel": meta.get("channel", ""),
            "duration": meta.get("duration"),
            "views": meta.get("views"),
            "upload_date": meta.get("upload_date", ""),
        })
        await self.store.update_item(item_id, meta_json=stored_meta)

        # Artifact: metadata
        await self.store.add_artifact(
            item_id, kind="metadata", path=source_url, meta=meta,
        )
        artifacts.append({"kind": "metadata", "path": source_url, "meta": meta})

        # Artifact: thumbnail (if downloaded)
        thumbnail = result.get("thumbnail")
        if thumbnail and Path(thumbnail).exists():
            await self.store.add_artifact(
                item_id, kind="thumbnail", path=thumbnail,
                meta={"source": "youtube"},
            )
            artifacts.append({
                "kind": "thumbnail", "path": thumbnail,
                "meta": {"source": "youtube"},
            })

        # Artifact: transcript
        content = result.get("content", "")
        if content:
            transcript_dir = self.storage_dir / "transcripts"
            transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = transcript_dir / f"{item_id}_transcript.txt"
            transcript_path.write_text(content, encoding="utf-8")

            transcript_meta = {
                "char_count": len(content),
                "language": "en",
            }
            await self.store.add_artifact(
                item_id, kind="transcript", path=str(transcript_path),
                meta=transcript_meta,
            )
            artifacts.append({
                "kind": "transcript", "path": str(transcript_path),
                "meta": transcript_meta,
            })

            # Store preview for item card
            preview = content[:200]
            stored_meta["preview"] = preview
            await self.store.update_item(item_id, meta_json=stored_meta)

        # Artifact: chapters (if available)
        chapters = meta.get("chapters", [])
        if chapters:
            chapters_lines: list[str] = []
            for ch in chapters:
                ts = format_timestamp(ch.get("start_time", 0))
                ch_title = ch.get("title", "")
                chapters_lines.append(f"[{ts}] {ch_title}")

            chapters_text = "\n".join(chapters_lines)
            chapters_dir = self.storage_dir / "chapters"
            chapters_dir.mkdir(parents=True, exist_ok=True)
            chapters_path = chapters_dir / f"{item_id}_chapters.txt"
            chapters_path.write_text(chapters_text, encoding="utf-8")

            await self.store.add_artifact(
                item_id, kind="chapters", path=str(chapters_path),
                meta={"count": len(chapters)},
            )
            artifacts.append({
                "kind": "chapters", "path": str(chapters_path),
                "meta": {"count": len(chapters)},
            })

        return artifacts


class WebProcessor(Processor):
    """Generic web-page URL processor — extracts readable text from HTML.

    Fetches the URL (SSRF-guarded against loopback/link-local/private hosts),
    then extracts the main content using readability-lxml. Falls back to a
    simple tag-stripping approach when readability-lxml is not installed.

    Produces a text artifact suitable for taosmd collection indexing so that
    agents granted the collection can query the page content.
    """

    _MAX_WEB_REDIRECTS = 5
    _MAX_WEB_BYTES = 10 * 1024 * 1024  # 10 MB
    _TOTAL_TIMEOUT = 60  # wall-clock deadline

    async def process(self, item: dict) -> list[dict]:
        item_id = item["id"]
        source_url = item.get("source_url", "")
        artifacts: list[dict] = []

        if not source_url:
            return artifacts

        import httpx
        from urllib.parse import urljoin

        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )

        # Fetch the page (SSRF-guarded, redirect-safe, size-capped, content-type gated).
        # Uses client.stream() so the body is never fully buffered before the cap runs
        # — a hostile server streaming a multi-GB text/html body is OOM-safe.
        async def _fetch() -> tuple[str, str, bytes]:
            current_url = source_url
            for _hop in range(self._MAX_WEB_REDIRECTS + 1):
                validate_url_or_raise(current_url)

                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30),
                    follow_redirects=False,
                ) as client:
                    async with client.stream("GET", current_url) as resp:
                        status_code = resp.status_code
                        content_type = resp.headers.get("content-type", "")

                        # Content-type gate: non-text responses end in error.
                        ct_base = content_type.split(";")[0].strip().lower()
                        if status_code >= 400:
                            resp.raise_for_status()
                        if ct_base and not ct_base.startswith("text/"):
                            raise ValueError(
                                f"Non-text content-type {content_type!r} "
                                f"for {source_url!r} — only text/* is supported"
                            )

                        # Redirect: grab Location, update URL, continue loop.
                        if status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                            current_url = urljoin(current_url, resp.headers["location"])
                            # fall through → exit with → _hop advances
                        else:
                            # Read body with size cap — streaming, so the cap stops OOM.
                            body_chunks: list[bytes] = []
                            total = 0
                            async for chunk in resp.aiter_bytes(8192):
                                total += len(chunk)
                                if total > self._MAX_WEB_BYTES:
                                    raise ValueError(
                                        f"Response body exceeds {self._MAX_WEB_BYTES} bytes "
                                        f"for {source_url!r}"
                                    )
                                body_chunks.append(chunk)
                            encoding = resp.encoding or "utf-8"
                            return content_type, encoding, b"".join(body_chunks)
            else:
                raise SsrfBlockedError(
                    f"too many redirects fetching {source_url!r}"
                )

        try:
            content_type, encoding, body = await asyncio.wait_for(
                _fetch(), timeout=self._TOTAL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ValueError(
                f"Timed out after {self._TOTAL_TIMEOUT}s fetching {source_url!r}"
            )

        html = body.decode(encoding, errors="replace")

        if not html:
            return artifacts

        # Extract readable text
        content = _extract_readable_text(html, source_url)

        # Extract title from <title> tag if item has no title
        import html as _html_mod
        title = item.get("title", "")
        if not title or title == source_url:
            import re
            m = re.search(
                r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE,
            )
            if m:
                title = _html_mod.unescape(m.group(1)).strip()[:200]
                await self.store.update_item(item_id, title=title)

        # Artifact: metadata
        page_meta = {
            "char_count": len(content),
            "content_type": content_type,
        }
        await self.store.add_artifact(
            item_id, kind="metadata", path=source_url, meta=page_meta,
        )
        artifacts.append({
            "kind": "metadata", "path": source_url, "meta": page_meta,
        })

        # Artifact: extracted text
        if content:
            text_dir = self.storage_dir / "text"
            text_dir.mkdir(parents=True, exist_ok=True)
            text_path = text_dir / f"{item_id}_web.txt"
            text_path.write_text(content, encoding="utf-8")

            text_meta = {
                "char_count": len(content),
                "source": "readability",
            }
            await self.store.add_artifact(
                item_id, kind="text", path=str(text_path), meta=text_meta,
            )
            artifacts.append({
                "kind": "text", "path": str(text_path), "meta": text_meta,
            })

            # Store preview
            preview = content[:200]
            stored_meta = json.loads(item.get("meta_json", "{}"))
            stored_meta["preview"] = preview
            await self.store.update_item(item_id, meta_json=stored_meta)

        return artifacts


def _read_lora_proxy_url(config_path: Path) -> str:
    """Read ``lora_ingest_proxy_url`` from config.yaml without side effects.

    Deliberately not ``load_config()``: that function persists a legacy
    litellm_port pin when one is missing, which would make a background
    Library ingest rewrite the user's configuration file.
    """
    if not config_path.exists():
        return ""
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        logger.warning("Could not read %s for the LoRA ingest proxy", config_path, exc_info=True)
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("lora_ingest_proxy_url", "") or "")


class CivitaiProcessor(Processor):
    """Civitai model-page URL processor -- delegates to the LoRA Studio ingest job.

    A url:civitai library item never gets its own file storage; the LoRA row
    (and its safetensors + previews) lives under models_root()/loras/, owned
    by LoRA Studio. This processor creates that row, runs the same ingest
    job POST /api/loras/ingest uses, and links the resulting lora_id back
    onto the library item.

    Unlike WebProcessor, the 10 MB cap does not apply here -- LoRA
    safetensors files are routinely hundreds of MB.
    """

    async def process(self, item: dict) -> list[dict]:
        item_id = item["id"]
        source_url = item.get("source_url", "")
        artifacts: list[dict] = []
        if not source_url:
            return artifacts

        from tinyagentos.lora_store import LoraStore
        from tinyagentos.routes.lora_studio import (
            CivitaiUrlError,
            lora_slug,
            parse_civitai_url,
            run_civitai_ingest,
        )

        try:
            model_id, url_slug, version_id = parse_civitai_url(source_url)
        except CivitaiUrlError as e:
            raise ValueError(str(e)) from e

        lora_id = f"lora-{lora_slug(url_slug, model_id)}"
        data_dir = self.storage_dir.parent
        lora_store = LoraStore(data_dir / "loras.db")
        await lora_store.init()
        try:
            await lora_store.create_pending(
                lora_id, source_url=source_url,
                civitai_model_id=model_id, civitai_version_id=version_id,
            )

            # Read the single key directly rather than via load_config():
            # load_config persists a legacy litellm_port pin as a side effect,
            # so calling it here would let a background ingest rewrite the
            # user's config.yaml.
            proxy_url = _read_lora_proxy_url(data_dir / "config.yaml")

            # Any failure raises -- let it propagate so run_pipeline marks
            # this library item as "error", same as YouTubeProcessor.
            await run_civitai_ingest(lora_store, lora_id, proxy_url)
            row = await lora_store.get(lora_id)
        finally:
            await lora_store.close()

        if row and not item.get("title") and row.get("name"):
            await self.store.update_item(item_id, title=row["name"])

        meta = {"lora_id": lora_id, "status": (row or {}).get("status", "")}
        await self.store.add_artifact(item_id, kind="lora", path="", meta=meta)
        artifacts.append({"kind": "lora", "path": "", "meta": meta})

        stored_meta = json.loads(item.get("meta_json", "{}"))
        stored_meta["lora_id"] = lora_id
        await self.store.update_item(item_id, meta_json=stored_meta)

        return artifacts


def _extract_readable_text(html: str, source_url: str = "") -> str:
    """Extract the main readable content from an HTML page.

    Uses readability-lxml when available; falls back to simple tag-stripping.
    """
    try:
        from readability import Document
        doc = Document(html)
        content = doc.summary()
        # Strip remaining HTML from readability output
        import re
        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 100:
            return text
    except ImportError:
        logger.debug("readability-lxml not installed — using simple extractor")
    except Exception:
        logger.warning("readability extraction failed for %s", source_url,
                       exc_info=True)

    # Fallback: simple tag-stripping (from knowledge_ingest._extract_text_readability)
    import re
    cleaned = re.sub(
        r"<(script|style)[^>]*>.*?</(script|style)>", "", html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Processor registry
# ---------------------------------------------------------------------------

_PROCESSORS: dict[str, type[Processor]] = {
    "file": FileProcessor,
    "text": TextProcessor,
    "pdf": PdfProcessor,
    "image": ImageProcessor,
    "url:youtube": YouTubeProcessor,
    "url:web": WebProcessor,
    "url:civitai": CivitaiProcessor,
}


def get_processor(kind: str, store: LibraryStore,
                  storage_dir: Path) -> Processor:
    """Return a processor for the given kind, falling back to FileProcessor."""
    cls = _PROCESSORS.get(kind, FileProcessor)
    return cls(store, storage_dir)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


async def run_pipeline(
    store: LibraryStore,
    item_id: str,
    storage_dir: Path,
) -> None:
    """Run the ingest pipeline for one item.

    Steps:
    1. Mark item as 'processing'
    2. Determine processor from item kind
    3. Run file + kind-specific processors
    4. Collect artifacts
    5. Mark item as 'ready' (or 'error')
    """
    item = await store.get_item(item_id)
    if not item:
        return

    kind = item["kind"]

    # If item has a storage_path that points to a missing file, fail early
    # (dropped/moved/corrupt source must not silently look successful).
    storage_path = item.get("storage_path", "")
    source_url = item.get("source_url", "")
    if storage_path and not source_url:
        sp = Path(storage_path)
        if not sp.exists():
            logger.warning(
                "Library pipeline: source file missing for item %s: %s",
                item_id, storage_path,
            )
            await store.update_item_status(item_id, "error")
            await store.update_item(
                item_id,
                meta_json={
                    **json.loads(item.get("meta_json", "{}")),
                    "error": f"Source file not found: {storage_path}",
                },
            )
            return

    try:
        await store.update_item_status(item_id, "processing")

        # Stage 1: basic file metadata (always)
        file_proc = FileProcessor(store, storage_dir)
        await file_proc.process(item)

        # Stage 2: kind-specific processor
        proc = get_processor(kind, store, storage_dir)
        if not isinstance(proc, FileProcessor):
            await proc.process(item)

        await store.update_item_status(item_id, "ready")
    except Exception:
        logger.exception("Library pipeline failed for item %s (kind=%s)",
                         item_id, kind)
        await store.update_item_status(item_id, "error")
        await store.update_item(
            item_id,
            meta_json={
                **json.loads(item.get("meta_json", "{}")),
                "error": f"Pipeline failed for kind={kind}",
            },
        )
