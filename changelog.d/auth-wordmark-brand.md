### Changed: the sign-in and setup pages carry the taOS wordmark

- The auth pages previously showed a drawn mark — a rounded square with a
  centre dot and an X through it. An X in a box on a sign-in screen reads as
  an error badge or a close affordance rather than a logo, so the brand is now
  set as type: the product name in the page's own font stack.
- The mark stays plain ASCII, so the pages keep the property the inline SVG was
  introduced for — no webfont to fetch, and no code point that can render as a
  missing-glyph box on a device without a covering font. These pages are
  deliberately JS-free and CDN-free, so a text glyph would have lost that.
- The setup page keeps its welcome in the subheading; no copy is dropped.
- `tests/test_auth_brand_mark.py` anchors on the `.brand` block rather than the
  whole page, and fails both on the drawn mark returning and on a regression to
  a bare Unicode glyph.
