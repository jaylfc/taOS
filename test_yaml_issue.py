import tempfile
from pathlib import Path
from tinyagentos.library_pipeline import detect_kind

# Create test files in a temp directory
with tempfile.TemporaryDirectory() as tmpdir:
    tmpdir = Path(tmpdir)
    
    # Create .yaml file
    yaml_file = tmpdir / "test.yaml"
    yaml_file.write_text("---\ntitle: Test YAML Document\ncontent: Hello World\n")
    
    # Create .log file
    log_file = tmpdir / "test.log"
    log_file.write_text("2026-01-01 10:00:00 INFO Application started\n")
    
    print("Test: .yaml and .log file kind detection")
    print(f"  test.yaml detected as: {detect_kind(file_path=str(yaml_file))}")
    print(f"  test.log detected as: {detect_kind(file_path=str(log_file))}")
    
    # Check if they're correctly identified as "text"
    yaml_kind = detect_kind(file_path=str(yaml_file))
    log_kind = detect_kind(file_path=str(log_file))
    
    if yaml_kind == "text":
        print("  ✓ test.yaml correctly detected as 'text'")
    else:
        print(f"  ✗ test.yaml incorrectly detected as '{yaml_kind}', should be 'text'")
    
    if log_kind == "text":
        print("  ✓ test.log correctly detected as 'text'")
    else:
        print(f"  ✗ test.log incorrectly detected as '{log_kind}', should be 'text'")
    
    if yaml_kind == "text" and log_kind == "text":
        print("\n✓ All tests passed: .yaml and .log files are correctly detected as 'text' kind")
    else:
        print("\n✗ Tests failed: .yaml or .log files are not correctly detected as 'text' kind")
