### Fixed

- Image shrinker: bound encoded data URL size before acquiring a decode slot (MAX_ENCODED_CHARS=64MB), preventing huge uploads from stalling other image requests.
- Image shrinker: transparent palette GIFs resized beyond MAX_LONG_EDGE are now saved as PNG to preserve alpha, instead of losing transparency when re-saved as GIF.
- Image shrinker: decode slot test now verifies base64.b64decode is called while _DECODE_SLOTS is held.