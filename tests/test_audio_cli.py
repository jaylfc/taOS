from __future__ import annotations

import json

import pytest

from tinyagentos.audio_cli import (
    parse_get_volume,
    parse_pw_dump,
    set_volume_argv,
    set_mute_argv,
    set_default_argv,
)


def test_parse_get_volume_plain():
    """Test parsing plain volume output."""
    text = "Volume: 0.62"
    result = parse_get_volume(text)
    assert result == {"volume": 0.62, "muted": False}


def test_parse_get_volume_muted():
    """Test parsing volume output with muted flag."""
    text = "Volume: 0.75 [MUTED]"
    result = parse_get_volume(text)
    assert result == {"volume": 0.75, "muted": True}


def test_parse_get_volume_garbage():
    """Test parsing invalid volume output returns None."""
    text = "garbage output"
    result = parse_get_volume(text)
    assert result is None


def test_parse_get_volume_empty():
    """Test parsing empty output returns None."""
    text = ""
    result = parse_get_volume(text)
    assert result is None


def test_parse_get_volume_missing_volume():
    """Test parsing output without volume returns None."""
    text = "Volume:"
    result = parse_get_volume(text)
    assert result is None


def test_parse_get_volume_invalid_number():
    """Test parsing volume output with invalid number returns None."""
    text = "Volume: abc"
    result = parse_get_volume(text)
    assert result is None


def test_parse_pw_dump_complete():
    """Test parsing pw-dump with complete data."""
    json_text = json.dumps([
        {
            "type": "PipeWire:Interface:Node",
            "name": "sink-1",
            "description": "Default Sink",
            "props": {"media.class": "Audio/Sink"},
        },
        {
            "type": "PipeWire:Interface:Node",
            "name": "source-1",
            "description": "Default Source",
            "props": {"media.class": "Audio/Source"},
        },
        {
            "type": "PipeWire:Interface:Node",
            "name": "video-1",
            "description": "Video Node",
            "props": {"media.class": "Audio/Video"},
        },
        {
            "type": "PipeWire:Interface:Metadata",
            "props": {
                "metadata.name": "default",
                "default.audio.sink": {"name": "sink-1"},
                "default.audio.source": {"name": "source-1"},
            },
        },
    ])
    result = parse_pw_dump(json_text)
    assert result == {
        "sinks": [
            {"id": "sink-1", "name": "sink-1", "description": "Default Sink"}
        ],
        "sources": [
            {"id": "source-1", "name": "source-1", "description": "Default Source"}
        ],
        "default_sink": "sink-1",
        "default_source": "source-1",
    }


def test_parse_pw_dump_no_metadata():
    """Test parsing pw-dump without metadata objects."""
    json_text = json.dumps([
        {
            "type": "PipeWire:Interface:Node",
            "name": "sink-1",
            "description": "Default Sink",
            "props": {"media.class": "Audio/Sink"},
        },
        {
            "type": "PipeWire:Interface:Node",
            "name": "source-1",
            "description": "Default Source",
            "props": {"media.class": "Audio/Source"},
        },
    ])
    result = parse_pw_dump(json_text)
    assert result == {
        "sinks": [
            {"id": "sink-1", "name": "sink-1", "description": "Default Sink"}
        ],
        "sources": [
            {"id": "source-1", "name": "source-1", "description": "Default Source"}
        ],
        "default_sink": None,
        "default_source": None,
    }


def test_parse_pw_dump_invalid_json():
    """Test parsing invalid JSON returns empty result."""
    json_text = "invalid json"
    result = parse_pw_dump(json_text)
    assert result == {
        "sinks": [],
        "sources": [],
        "default_sink": None,
        "default_source": None,
    }


def test_set_volume_argv_valid_id():
    """Test set_volume_argv with valid node ID."""
    argv = set_volume_argv("123", 0.75)
    assert argv == ["wpctl", "set-volume", "123", "0.75"]


def test_set_volume_argv_default_sink():
    """Test set_volume_argv with @DEFAULT_AUDIO_SINK@."""
    argv = set_volume_argv("@DEFAULT_AUDIO_SINK@", 0.75)
    assert argv == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.75"]


def test_set_volume_argv_clamping_low():
    """Test set_volume_argv clamps volume to 0.0."""
    argv = set_volume_argv("123", -1.0)
    assert argv == ["wpctl", "set-volume", "123", "0.00"]


def test_set_volume_argv_clamping_high():
    """Test set_volume_argv clamps volume to 1.50."""
    argv = set_volume_argv("123", 2.0)
    assert argv == ["wpctl", "set-volume", "123", "1.50"]


def test_set_volume_argv_formatting():
    """Test set_volume_argv formats volume as %.2f."""
    argv = set_volume_argv("123", 0.5)
    assert argv == ["wpctl", "set-volume", "123", "0.50"]


def test_set_volume_argv_invalid_node():
    """Test set_volume_argv raises ValueError for invalid node."""
    with pytest.raises(ValueError, match="Invalid node: @INVALID@"):
        set_volume_argv("@INVALID@", 0.5)


def test_set_volume_argv_too_large():
    """Test set_volume_argv raises ValueError for too large node ID."""
    with pytest.raises(ValueError, match="Invalid node: 1234567"):
        set_volume_argv("1234567", 0.5)


def test_set_volume_argv_empty():
    """Test set_volume_argv raises ValueError for empty node."""
    with pytest.raises(ValueError, match="Invalid node: "):
        set_volume_argv("", 0.5)


def test_set_mute_argv_valid():
    """Test set_mute_argv with valid node."""
    argv = set_mute_argv("123", True)
    assert argv == ["wpctl", "set-muted", "123", "true"]


def test_set_mute_argv_false():
    """Test set_mute_argv with false."""
    argv = set_mute_argv("123", False)
    assert argv == ["wpctl", "set-muted", "123", "false"]


def test_set_mute_argv_default_sink():
    """Test set_mute_argv with @DEFAULT_AUDIO_SINK@."""
    argv = set_mute_argv("@DEFAULT_AUDIO_SINK@", True)
    assert argv == ["wpctl", "set-muted", "@DEFAULT_AUDIO_SINK@", "true"]


def test_set_mute_argv_invalid():
    """Test set_mute_argv raises ValueError for invalid node."""
    with pytest.raises(ValueError, match="Invalid node: @INVALID@"):
        set_mute_argv("@INVALID@", False)


def test_set_default_argv_valid():
    """Test set_default_argv with valid node."""
    argv = set_default_argv("123")
    assert argv == ["wpctl", "set-default", "123"]


def test_set_default_argv_default_sink():
    """Test set_default_argv with @DEFAULT_AUDIO_SINK@."""
    argv = set_default_argv("@DEFAULT_AUDIO_SINK@")
    assert argv == ["wpctl", "set-default", "@DEFAULT_AUDIO_SINK@"]


def test_set_default_argv_invalid():
    """Test set_default_argv raises ValueError for invalid node."""
    with pytest.raises(ValueError, match="Invalid node: @INVALID@"):
        set_default_argv("@INVALID@")
