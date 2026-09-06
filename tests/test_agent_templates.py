from tinyagentos.agent_templates import BUILTIN_TEMPLATES


def test_builtin_templates_have_no_runtime_fields():
    banned = ("model", "framework", "memory_limit", "cpu_limit")
    for tpl in BUILTIN_TEMPLATES:
        for b in banned:
            assert b not in tpl, f"{tpl.get('id', '?')} still has {b!r}"


def test_builtin_templates_have_persona_fields():
    for tpl in BUILTIN_TEMPLATES:
        assert "id" in tpl
        assert "name" in tpl
        assert "system_prompt" in tpl
