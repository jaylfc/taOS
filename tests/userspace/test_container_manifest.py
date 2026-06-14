import pytest
from tinyagentos.userspace.package import parse_manifest, PackageError

VALID_CONTAINER = (
    "id: echo\nname: Echo\nversion: 1.0.0\napp_type: container\n"
    "container:\n  image: docker.io/hashicorp/http-echo:latest\n  ports: [5678]\n"
)


def test_valid_container_manifest_roundtrip():
    m = parse_manifest(VALID_CONTAINER)
    assert m["container"]["image"] == "docker.io/hashicorp/http-echo:latest"
    assert m["container"]["ports"] == [5678]


def test_container_app_no_container_block():
    with pytest.raises(PackageError, match="container"):
        parse_manifest("id: x\nname: X\nversion: 1\napp_type: container\n")


def test_container_block_missing_image():
    with pytest.raises(PackageError, match="container.image"):
        parse_manifest(
            "id: x\nname: X\nversion: 1\napp_type: container\n"
            "container:\n  ports: [8080]\n"
        )


@pytest.mark.parametrize("ports_yaml,label", [
    ("", "missing"),
    ("  ports: []\n", "empty list"),
    ("  ports: [x]\n", "non-int element"),
    ("  ports: [true]\n", "bool element"),
])
def test_container_block_bad_ports(ports_yaml, label):
    yaml_text = (
        "id: x\nname: X\nversion: 1\napp_type: container\n"
        "container:\n  image: foo\n" + ports_yaml
    )
    with pytest.raises(PackageError, match="container.ports"):
        parse_manifest(yaml_text)


def test_web_app_no_container_block_parses_fine():
    m = parse_manifest("id: t\nname: T\nversion: 1\napp_type: web\n")
    assert m["app_type"] == "web"
