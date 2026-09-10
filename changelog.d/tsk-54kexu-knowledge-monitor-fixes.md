### Fixed

- Restored LIMIT/OFFSET pagination in KnowledgeStore.list_items so callers no longer load the entire knowledge table into memory
- MonitorService.poll_item now refreshes item content with extracted text via the ingest readability extractor instead of skipping updates or storing raw HTML
- Implemented stop_after_days: items whose created_at exceeds the monitor's stop_after_days are no longer polled and are marked with status=stopped
