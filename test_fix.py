import mimetypes
from pathlib import Path
import tempfile
from tinyagentos.library_pipeline import detect_kind

# Create test files in a temp directory
with tempfile.TemporaryDirectory() as tmpdir:
    # Create test.yaml file
    yaml_file = Path(tmpdir) / "test.yaml"
    yaml_file.write_text("---\ntitle: Test YAML\n")
    
    # Create test.log file  
    log_file = Path(tmpdir) / "test.log"
    log_file.write_text("2026-01-01 INFO Test log message\n")
    
    # Create test.py file
    py_file = Path(tmpdir) / "test.py"
    py_file.write_text("print('Hello World')\n")
    
    # Create test.toml file
    toml_file = Path(tmpdir) / "test.toml"
    toml_file.write_text("key = \"value\"\n")
    
    print("Testing file kind detection:")
    for f in [yaml_file, log_file, py_file, toml_file]:
        kind = detect_kind(file_path=str(f))
        mime_type, _ = mimetypes.guess_type(f.name)
        print(f"  {f.name:20} -> {kind} (mimetype: {mime_type})")
        
        if kind == "file":
            print(f"    ERROR: Detected as 'file' - should be 'text'")
