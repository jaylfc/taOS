"""The agent model-assignment guard rejects non-chat (embedding) models.

Assigning an embedding model as an agent's chat model produces undefined,
looping output instead of a reply (reported in #1740). The guard returns a
400 for embedding/reranker slugs and passes chat-capable models through.
"""
from __future__ import annotations

import json

import pytest

from tinyagentos.routes.agents import (
    _reject_non_chat_model,
    _small_context_warning,
)


@pytest.mark.parametrize(
    "model_id",
    [
        "qwen3-embedding-0.6b",
        "nomic-embed-text",
        "mxbai-embed-large",
        "bge-large-en",
        "arctic-embed-s",
    ],
)
def test_embedding_models_are_rejected(model_id):
    rejection = _reject_non_chat_model(model_id)
    assert rejection is not None
    assert rejection.status_code == 400
    body = json.loads(rejection.body)
    assert body["reason"] == "not_chat_capable"
    assert body["model"] == model_id


@pytest.mark.parametrize(
    "model_id",
    [
        "qwen2.5-1.5b-rkllm",
        "llama-3.1-8b-instruct",
        "gpt-4o",
        "qwen2.5-coder-7b",
        "",
    ],
)
def test_chat_capable_models_pass(model_id):
    # None means "allowed"; the empty string is a no-op (validated elsewhere).
    assert _reject_non_chat_model(model_id) is None


class _FakeManifest:
    def __init__(self, context_window):
        self.id = "m"
        self.type = "model"
        self.context_window = context_window


class _FakeRegistry:
    def __init__(self, manifest):
        self._manifest = manifest

    def get(self, model_id):
        return self._manifest

    def list_available(self, type_filter=None):
        return []


def test_small_context_model_warns():
    warning = _small_context_warning(_FakeRegistry(_FakeManifest(4096)), "qwen2.5-1.5b-rkllm")
    assert warning is not None
    assert "4096" in warning


def test_adequate_context_model_does_not_warn():
    assert _small_context_warning(_FakeRegistry(_FakeManifest(32768)), "gemma-4-e4b") is None


def test_unknown_model_does_not_warn():
    # A cloud/aliased model resolves to no local manifest -> no advisory.
    assert _small_context_warning(_FakeRegistry(None), "gpt-4o") is None


def test_missing_registry_does_not_warn():
    assert _small_context_warning(None, "anything") is None
