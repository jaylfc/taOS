### Fixed

Fix App Studio preview security vulnerabilities by migrating from regex-based HTML surgery to lxml.html parsing.

- Script tags with `</script>` in JS strings no longer break out of the block
- Style tags with `</style>` in CSS strings no longer break out of the block  
- Unquoted attributes (e.g., `img src=img.png`) are now properly rewritten to data URIs
- Data-URI `url()` values in CSS survive parsing without truncation
- Byte budget enforcement moved to post-serialization for consistency

The preview HTML is now parsed with lxml.html, which safely handles edge cases that regex patterns cannot match, such as nested tags, quoted attributes containing `>`, and proper escaping of special sequences.
