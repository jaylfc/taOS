import tempfile
from pathlib import Path
from tinyagentos.library_pipeline import detect_kind

# Test the issue: .yaml and .log files should be detected as "text" but currently are "file"
with tempfile.TemporaryDirectory() as tmpdir:
    tmpdir = Path(tmpdir)
    
    # Create test files
    yaml_file = tmpdir / "test.yaml"
    yaml_file.write_text("---\ntitle: Test YAML Document\ncontent: Hello World\n")
    
    log_file = tmpdir / "test.log"
    log_file.write_text("2026-01-01 10:00:00 INFO Application started\n")
    
    py_file = tmpdir / "test.py"
    py_file.write_text("print(\"Hello Python\")\n")
    
    toml_file = tmpdir / "config.toml"
    toml_file.write_text("key = \"value\"\n")
    
    print("Current detect_kind results:")
    for f in [yaml_file, log_file, py_file, toml_file]:
        kind = detect_kind(file_path=str(f))
        print(f"  {f.name:20} -> {kind}")
    
    # The issue: .yaml and .log should be "text", but they're "file"
    # .py and .toml are correctly "text" with our fix
    print("\nExpected results:")
    print("  test.yaml            -> text")
    print("  test.log             -> text")
    print("  test.py              -> text")
    print("  config.toml          -> text")
