### Fixed
- Logging configuration now applies on all start paths (python -m tinyagentos, uvicorn --factory, test client). Named logger INFO lines now reach the journal when running as a system service. Gaming detector tests no longer experience clock interference from logging setup.
