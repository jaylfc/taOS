"""Watch projection for project canvas elements.

A versioned, renderer-neutral projection of ``project_canvas_elements`` for
watch clients (S7a).  Pure data: takes a list of element dicts (as returned by
``ProjectCanvasStore.list_elements``) and returns a JSON-serializable dict.

Design rules (bus 1319):

* ``payload`` is freeform JSON, NOT guaranteed to be a dict.  Every read is
  defensive, so one hostile row degrades to a placeholder rather than raising
  and aborting the whole projection.
* No coupling to renderer-specific payload shapes (tldraw OR Excalidraw; #2132
  migration in flight).  Only generic keys are consulted.
* Follows the ``tinyagentos/projects/canvas/snapshotter.py`` precedent: a
  standalone module of pure functions, no DB, no async, no broker.
"""
from __future__ import annotations

from typing import Any
import logging
from urllib.parse import urlparse

_VERSION = 1
logger = logging.getLogger(__name__)

_TEXT_CARD_KINDS = ("note", "text")
_DIAGRAM_KINDS = ("mermaid", "flowchart")


def build_watch_projection(elements: list[dict]) -> dict:
    """Build a versioned watch projection from canvas elements.

    Returns ``{"version": 1, "elements": [...]}`` where each entry is a
    renderer-neutral mapping of one canvas element.  Rows are ordered by
    ``z_index``.  Any row that cannot be projected degrades to a placeholder
    entry, so one bad row never drops the good rows.
    """
    projected: list[dict] = []
    for el in elements:
        try:
            projected.append(_project_element(el))
        except Exception:
            logger.debug("watch projection: element degraded to placeholder", exc_info=True)
            projected.append(_placeholder_entry(el, "malformed element"))
    projected.sort(key=lambda e: (e.get("z_index") or 0))
    return {"version": _VERSION, "elements": projected}


def _project_element(el: dict) -> dict:
    if not isinstance(el, dict):
        return _placeholder_entry(el, "malformed element")
    kind = _str_or(el.get("kind"), "unknown")
    if kind in _TEXT_CARD_KINDS:
        return _text_card(el, kind)
    if kind == "image":
        return _thumbnail(el)
    if kind == "link":
        return _link_row(el)
    if kind in _DIAGRAM_KINDS:
        return _placeholder_entry(el, "diagram - open on phone")
    if kind == "user_shape":
        return _placeholder_entry(el, "user shape")
    return _placeholder_entry(el, kind)


def _base_entry(el: dict, kind: str, entry_type: str) -> dict:
    if not isinstance(el, dict):
        return {"id": "", "kind": kind, "type": entry_type, "z_index": 0}
    return {
        "id": _str_or(el.get("id"), ""),
        "kind": kind,
        "type": entry_type,
        "z_index": _int_or(el.get("z_index"), 0),
    }


def _text_card(el: dict, kind: str) -> dict:
    entry = _base_entry(el, kind, "text_card")
    entry["text"] = _payload_text(el)
    return entry


def _thumbnail(el: dict) -> dict:
    entry = _base_entry(el, "image", "thumbnail")
    entry["alt"] = _payload_str(el, "alt")
    entry["file_id"] = _payload_str(el, "file_id")
    return entry


def _link_row(el: dict) -> dict:
    entry = _base_entry(el, "link", "link_row")
    payload = el.get("payload")
    if isinstance(payload, dict):
        title = payload.get("title")
        url = payload.get("url")
    else:
        title = None
        url = None
    entry["title"] = _str_or(title, "")
    entry["domain"] = _domain_of(url)
    return entry


def _placeholder_entry(el: dict, label: str) -> dict:
    kind = _str_or(el.get("kind"), "unknown") if isinstance(el, dict) else "unknown"
    entry = _base_entry(el, kind, "placeholder")
    entry["label"] = label
    return entry


def _payload_text(el: dict) -> str:
    """Best-effort text content from an element's payload.

    ``payload`` is whatever JSON was stored, which is not guaranteed to be an
    object: a list or bare string decodes truthy and would make ``.get()``
    raise, taking the whole projection down over one odd row.
    """
    payload = el.get("payload")
    if isinstance(payload, dict):
        for key in ("text", "title", "label", "source", "url", "caption"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    elif isinstance(payload, str) and payload.strip():
        return payload
    return ""


def _payload_str(el: dict, key: str) -> str:
    payload = el.get("payload")
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _domain_of(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _str_or(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _int_or(value: Any, default: int) -> int:
    """Coerce a stored z_index to an int, or fall back.

    ``bool`` is excluded deliberately: it is an ``int`` subclass, so ``True``
    would sail through as ``1`` and silently reorder a shape.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value
