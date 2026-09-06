"""Tests for tinyagentos.gpu_queue_flag — taOS #1864 Slice A1."""
from __future__ import annotations

import pytest

from tinyagentos.gpu_queue_flag import gpu_queue_mode, GPU_QUEUE_MODES


def test_gpu_queue_mode_default_off(monkeypatch):
    monkeypatch.delenv("TAOS_GPU_QUEUE", raising=False)
    assert gpu_queue_mode() == "off"


@pytest.mark.parametrize("value", ["shadow", "on", "OFF", " Shadow "])
def test_gpu_queue_mode_env_values(monkeypatch, value):
    monkeypatch.setenv("TAOS_GPU_QUEUE", value)
    assert gpu_queue_mode() == value.strip().lower()


def test_gpu_queue_mode_invalid_coerces_off(monkeypatch):
    monkeypatch.setenv("TAOS_GPU_QUEUE", "bananas")
    assert gpu_queue_mode() == "off"


def test_modes_tuple_is_locked():
    assert GPU_QUEUE_MODES == ("off", "shadow", "on")
