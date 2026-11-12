import hashlib
import json

from tinyagentos.auth import AuthManager


def _bindings(path_mgr: AuthManager):
    path = path_mgr._local_token_agent_path()
    return json.loads(path.read_text())


class TestCanonicalLocalTokenBindings:
    def test_mint_with_canonical_id_stores_dict_shape_and_reads_both_shapes(self, tmp_path):
        mgr = AuthManager(tmp_path)
        token = mgr.mint_agent_local_token("agent-a", canonical_id="cid-a")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        bindings = _bindings(mgr)
        assert bindings[token_hash] == {"name": "agent-a", "canonical_id": "cid-a"}
        assert mgr.get_local_token_agent(token) == "agent-a"
        assert mgr.get_local_token_binding(token) == ("agent-a", "cid-a")

        legacy_hash = hashlib.sha256(b"legacy-token").hexdigest()
        bindings[legacy_hash] = "legacy-agent"
        mgr._local_token_agent_path().write_text(json.dumps(bindings))
        assert mgr.get_local_token_agent("legacy-token") == "legacy-agent"
        assert mgr.get_local_token_binding("legacy-token") == ("legacy-agent", None)

    def test_exclusive_mint_and_unbind_drop_both_shapes(self, tmp_path):
        mgr = AuthManager(tmp_path)
        old = mgr.mint_agent_local_token("agent-a", canonical_id="cid-a")
        bindings = _bindings(mgr)
        legacy_hash = hashlib.sha256(b"legacy-a").hexdigest()
        bindings[legacy_hash] = "agent-a"
        mgr._local_token_agent_path().write_text(json.dumps(bindings))
        assert mgr.validate_local_token("legacy-a") is True

        new = mgr.mint_agent_local_token("agent-a", canonical_id="cid-b")
        assert mgr.validate_local_token(old) is False
        assert mgr.validate_local_token("legacy-a") is False
        assert mgr.validate_local_token(new) is True
        assert mgr.get_local_token_binding(new) == ("agent-a", "cid-b")

        mgr.unbind_local_token_agent("agent-a")
        assert mgr.validate_local_token(new) is False
        assert mgr.get_local_token_binding(new) is None
