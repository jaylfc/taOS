### Fixed

- Move `base64.b64decode` inside `_DECODE_SLOTS` block so decoded bytes are not held while workers are queued
- Return unsupported image formats (those not in `_FORMAT_MEDIA`) unchanged right after `Image.open`, before any pixel work or thumbnail
- Convert palette mode 'P' to 'RGBA' (when transparency is present) or 'RGB' before `thumbnail` to ensure proper downsampling
- Convert 16-bit grayscale modes ('I;16', 'I') to 'L' before `thumbnail` to prevent Pillow errors