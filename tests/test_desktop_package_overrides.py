import json
import pathlib


DESKTOP_PACKAGE_JSON = pathlib.Path(__file__).resolve().parent.parent / "desktop" / "package.json"


def test_desktop_overrides_use_dollar_syntax_for_direct_deps():
    pkg = json.loads(DESKTOP_PACKAGE_JSON.read_text())
    deps = set(pkg.get("dependencies", {}).keys())
    dev_deps = set(pkg.get("devDependencies", {}).keys())
    direct_deps = deps | dev_deps

    overrides = pkg.get("overrides", {})

    bad = []
    for key, value in overrides.items():
        if key in direct_deps and value != f"${key}":
            bad.append(f"{key}={value}")

    assert not bad, f"override for direct dep must use \"$<key>\"; got: {', '.join(bad)}"
