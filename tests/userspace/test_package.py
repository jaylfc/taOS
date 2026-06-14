import io, zipfile
import pytest
from tinyagentos.userspace.package import parse_manifest, extract_package, PackageError


def _zip(manifest: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


WEB_MANIFEST = """
id: todo
name: Todo
version: 1.0.0
app_type: web
entry: index.html
icon: icon.png
permissions: [app.net]
"""


def test_parse_valid_web_manifest():
    m = parse_manifest(WEB_MANIFEST)
    assert m["id"] == "todo"
    assert m["app_type"] == "web"
    assert m["permissions"] == ["app.net"]


def test_native_app_type_rejected():
    with pytest.raises(PackageError, match="native"):
        parse_manifest(WEB_MANIFEST.replace("app_type: web", "app_type: native"))


def test_missing_required_field_rejected():
    with pytest.raises(PackageError, match="required"):
        parse_manifest("name: NoId\nversion: 1\napp_type: web\n")


def test_extract_writes_files(tmp_path):
    data = _zip(WEB_MANIFEST, {"index.html": "<h1>hi</h1>", "icon.png": "x"})
    manifest = extract_package(data, apps_root=tmp_path)
    app_dir = tmp_path / "todo"
    assert (app_dir / "index.html").read_text() == "<h1>hi</h1>"
    assert manifest["id"] == "todo"


def test_extract_rejects_path_traversal(tmp_path):
    data = _zip(WEB_MANIFEST, {"../evil.txt": "pwned"})
    with pytest.raises(PackageError, match="unsafe path"):
        extract_package(data, apps_root=tmp_path)
