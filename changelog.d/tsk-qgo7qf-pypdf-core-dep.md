### Fixed

- Add `pypdf>=6.17` to core dependencies and make `PdfProcessor` propagate
  extraction errors instead of swallowing them. Previously the missing import
  was silently ignored, causing every PDF to be indexed with empty text and
  marked ready. Now a failed extraction marks the item as error.
