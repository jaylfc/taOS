from pathlib import Path


def test_streaming_catalog_allowlist():
    streaming_dir = Path(__file__).resolve().parents[1] / "app-catalog" / "streaming"
    entries = sorted(
        p.name
        for p in streaming_dir.iterdir()
        if p.is_dir()
    )
    expected = ["neko-browser"]
    assert entries == expected, (
        "A new streaming app must be real (buildable) and added to this allowlist deliberately, "
        f"but got {entries}"
    )
