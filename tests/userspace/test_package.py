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


def test_parse_manifest_rejects_yaml_list():
    # Finding 5: safe_load returns a list -- must raise PackageError, not AttributeError.
    with pytest.raises(PackageError, match="mapping"):
        parse_manifest("- item1\n- item2\n")


def test_parse_manifest_rejects_yaml_scalar():
    # Finding 5: safe_load returns a scalar -- must raise PackageError.
    with pytest.raises(PackageError, match="mapping"):
        parse_manifest("just a string")


def test_extract_rejects_dot_member(tmp_path):
    # Finding 7: a zip member "." resolves to app_dir itself -- must raise PackageError,
    # not an IsADirectoryError when write_bytes is called on a directory.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", WEB_MANIFEST.strip())
        z.writestr("index.html", "<h1>ok</h1>")
        # Add a member whose name is just "." -- resolves to app_dir on extraction.
        z.writestr(".", "bad")
    with pytest.raises(PackageError, match="unsafe path"):
        extract_package(buf.getvalue(), apps_root=tmp_path)


def test_extract_rejects_zip_bomb(tmp_path, monkeypatch):
    # Zip-bomb defense: cap the declared uncompressed total low and confirm an
    # over-cap package is rejected before extraction.
    import tinyagentos.userspace.package as pkg
    monkeypatch.setattr(pkg, "_MAX_UNCOMPRESSED_BYTES", 8)
    data = _zip(WEB_MANIFEST, {"index.html": "<h1>more than eight bytes</h1>"})
    with pytest.raises(PackageError, match="uncompressed size too large"):
        extract_package(data, apps_root=tmp_path)
