### Fixed

- Web Studio: the saved-sites list loader now ignores stale responses when Retry and Save race. A request sequence counter ensures only the newest loadList result updates the list, so an older Retry response cannot overwrite a newer Save result or restore a cleared list error after a success.
