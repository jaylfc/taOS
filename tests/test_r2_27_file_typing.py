"""RED test for R2-27: File typing issue - .yaml and .log files fall to FileProcessor."""

import tempfile
from pathlib import Path
import pytest

from tinyagentos.library_pipeline import detect_kind


class TestFileTypingR2_27:
    """Test that .yaml and .log files (and similar) use TextProcessor, not FileProcessor."""

    def test_yaml_files_use_text_processor(self):
        """mimetypes returns application/yaml for .yaml -> should be 'text' kind."""
        assert detect_kind(file_path="test.yaml") == "text"
        assert detect_kind(file_path="test.YAML") == "text"
        assert detect_kind(file_path="config.yml") == "text"
        assert detect_kind(file_path="data.toml") == "text"

    def test_markdown_and_text_files_use_text_processor(self):
        """Explicit .md/.txt already in ext_map -> should be 'text' kind."""
        assert detect_kind(file_path="README.md") == "text"
        assert detect_kind(file_path="notes.txt") == "text"
        assert detect_kind(file_path="data.csv") == "text"
        assert detect_kind(file_path="config.json") == "text"
        assert detect_kind(file_path="settings.xml") == "text"
        assert detect_kind(file_path="page.html") == "text"

    def test_log_files_use_text_processor(self):
        """mimetypes returns None for .log, but audit says .log should use TextProcessor."""
        # .log is in the "similar" extensions that should use TextProcessor per the audit
        # Add .log to the explicit text-based extensions override
        assert detect_kind(file_path="app.log") == "text"
        assert detect_kind(file_path="errors.LOG") == "text"

    def test_py_files_should_still_be_file_processor(self):
        """mimetypes returns text/x-python for .py, but this should still be 'file' kind.
        
        This is an explicit override: mimetypes gets 'text/x-python' which is technically a text/*
        MIME type, but the audit says to keep explicit overrides for types mimetypes gets wrong.
        .py files should remain in FileProcessor (kind='file').
        """
        assert detect_kind(file_path="script.py") == "file"

    def test_image_and_archive_extensions_still_use_image_archive_processors(self):
        """Explicit overrides for image and archive types should remain."""
        assert detect_kind(file_path="photo.jpg") == "image"
        assert detect_kind(file_path="document.pdf") == "pdf"
        assert detect_kind(file_path="archive.zip") == "archive"

    def test_unknown_extension_falls_back_to_file_processor(self):
        """Unknown extensions should fall back to 'file' kind."""
        assert detect_kind(file_path="unknown.xyz") == "file"
        assert detect_kind(file_path="binary.bin") == "file"