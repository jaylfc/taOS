from tinyagentos.frameworks import recommended_framework


def test_recommended_framework_low_ram():
    assert recommended_framework(4096) == "picoclaw"


def test_recommended_framework_snapped_8gb():
    assert recommended_framework(7800) == "picoclaw"


def test_recommended_framework_high_ram():
    assert recommended_framework(16384) == "hermes"
