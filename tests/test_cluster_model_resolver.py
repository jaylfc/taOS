"""Unit tests for tinyagentos.cluster.model_resolver._model_matches.

The resolver uses a loose prefix match so wizard-picked ids like
``qwen2.5-7b`` still resolve against backend-reported ids like
``qwen2.5-7b-instruct-q4_k_m`` or ``qwen2.5-7b.gguf``. The rule must
NOT treat version-number dots as variant boundaries; ``qwen3`` and
``qwen3.5`` are different model families.
"""
from tinyagentos.cluster.model_resolver import _model_matches


class TestModelMatches:
    def test_exact_match(self):
        assert _model_matches("qwen3.5-4b", "qwen3.5-4b")

    def test_case_insensitive(self):
        assert _model_matches("QWEN3.5-4B", "qwen3.5-4b")

    def test_colon_and_underscore_normalised_to_dash(self):
        assert _model_matches("qwen3.5:4b", "qwen3.5-4b")
        assert _model_matches("qwen2_5-7b", "qwen2-5-7b")

    def test_variant_suffix_with_dash(self):
        # Backend reports a variant, wizard has the bare id
        assert _model_matches("qwen2.5-7b", "qwen2.5-7b-instruct-q4_k_m")
        # Reverse: wizard has the long form, backend has the short one
        assert _model_matches("qwen2.5-7b-instruct-q4_k_m", "qwen2.5-7b")

    def test_file_extension_matches(self):
        # Canonical file extensions must still resolve
        assert _model_matches("qwen2.5-7b", "qwen2.5-7b.gguf")
        assert _model_matches("qwen2.5-7b", "qwen2.5-7b.safetensors")
        assert _model_matches("qwen2.5-7b", "qwen2.5-7b.onnx")
        # Reverse
        assert _model_matches("qwen2.5-7b.gguf", "qwen2.5-7b")

    def test_version_number_is_not_an_extension(self):
        # THE REGRESSION: qwen3 is NOT a shorter alias for qwen3.5.
        # Before the fix, c.startswith(t + ".") treated qwen3.5 as a
        # variant of qwen3 and routed cross-family.
        assert not _model_matches("qwen3", "qwen3.5-4b")
        assert not _model_matches("qwen3.5-4b", "qwen3")
        assert not _model_matches("llama3", "llama3.1-8b")
        assert not _model_matches("llama3.1-8b", "llama3")
        assert not _model_matches("gemma2", "gemma2.5")

    def test_different_families_do_not_match(self):
        assert not _model_matches("qwen3-embedding", "qwen3.5-4b")
        assert not _model_matches("llama3", "qwen3")
        assert not _model_matches("mistral-7b", "mixtral-8x7b")

    def test_empty_inputs(self):
        assert not _model_matches("", "qwen3")
        assert not _model_matches("qwen3", "")
        assert not _model_matches("", "")

    def test_shared_prefix_without_boundary_does_not_match(self):
        # qwen vs qwen3 — no dash or valid extension separator
        assert not _model_matches("qwen", "qwen3")
        assert not _model_matches("qwen3", "qwen34")
