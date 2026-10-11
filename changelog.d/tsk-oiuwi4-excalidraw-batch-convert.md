### Fixed
- Convert all non-diagram skeletons in one batch so arrow bindings resolve
- Changed ExcalidrawBoard to batch all skeletons (non-diagram elements and diagram placeholders) into a single convertToExcalidrawElements call.
- This ensures arrow bindings (e.g., mindmap_edge) can resolve start/end ids within the same batch.
- Ready diagrams are spliced in after conversion while preserving z_index order across kinds.
- Added explanatory comment about why batching is necessary.