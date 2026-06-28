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


def test_network_permission_origins_validated():
    from tinyagentos.userspace.package import parse_manifest, PackageError
    import pytest
    base = "id: x\nname: X\nversion: 1.0.0\napp_type: web\n"
    ok = parse_manifest(base + "permissions:\n  - 'network:wss://irc-ws.chat.twitch.tv'\n  - 'network:https://*.pusher.com'\n  - 'network:https://youtube.googleapis.com:443'\n")
    assert "network:wss://irc-ws.chat.twitch.tv" in ok["permissions"]
    for bad in [
        "network:javascript:alert(1)",
        "network:wss://h.com; script-src 'unsafe-inline'",
        "network:wss://h.com/path",
        "network:ftp://h.com",
        "network:*",
        "network:'unsafe-inline'",
        "network:wss://h.com\n",
        "network:wss://h.com\n; script-src x",
    ]:
        with pytest.raises(PackageError):
            parse_manifest(base + "permissions: ['" + bad + "']\n")
    # gitar finding: `$` would match before a trailing newline; \Z must not.
    # NET_ORIGIN_RE is the canonical pattern in capabilities.py, reused here.
    from tinyagentos.userspace.capabilities import NET_ORIGIN_RE
    assert NET_ORIGIN_RE.match("wss://irc-ws.chat.twitch.tv")
    assert not NET_ORIGIN_RE.match("wss://evil.com\n")
    assert not NET_ORIGIN_RE.match("wss://evil.com\n; script-src 'unsafe-inline'")


def test_parse_valid_tui_manifest():
    m = parse_manifest(
        "id: mycli\nname: MyCLI\nversion: 1.0.0\napp_type: tui\ncommand: opencode\n"
    )
    assert m["id"] == "mycli"
    assert m["app_type"] == "tui"
    assert m["command"] == "opencode"
    assert m["args"] == []
    assert m["needs_project_dir"] is True
    assert m["env"] == {}


def test_parse_tui_manifest_with_optional_fields():
    m = parse_manifest(
        "id: mycli\nname: MyCLI\nversion: 1.0.0\napp_type: tui\n"
        "command: claude\n"
        "args: ['--verbose', '--fast']\n"
        "needs_project_dir: false\n"
        "env:\n  FOO: bar\n  BAZ: qux\n"
    )
    assert m["command"] == "claude"
    assert m["args"] == ["--verbose", "--fast"]
    assert m["needs_project_dir"] is False
    assert m["env"] == {"FOO": "bar", "BAZ": "qux"}


def test_tui_manifest_missing_command_rejected():
    with pytest.raises(PackageError, match="command"):
        parse_manifest(
            "id: mycli\nname: MyCLI\nversion: 1.0.0\napp_type: tui\n"
        )


def test_tui_manifest_empty_command_rejected():
    with pytest.raises(PackageError, match="command"):
        parse_manifest(
            "id: mycli\nname: MyCLI\nversion: 1.0.0\napp_type: tui\ncommand: ''\n"
        )


def test_unknown_app_type_rejected():
    with pytest.raises(PackageError, match="not allowed"):
        parse_manifest(
            "id: x\nname: X\nversion: 1.0.0\napp_type: native\nentry: x\n"
        )


def test_web_manifest_still_parses():
    m = parse_manifest(WEB_MANIFEST)
    assert m["id"] == "todo"
    assert m["app_type"] == "web"
    assert m["permissions"] == ["app.net"]
