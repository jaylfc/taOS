import re
import pytest
from tinyagentos.projects.ids import new_id, ID_PREFIXES


def test_new_id_format():
    for prefix in ID_PREFIXES:
        value = new_id(prefix)
        assert re.fullmatch(rf"{prefix}-[a-z2-7]{{6}}", value), value


def test_new_id_unique_across_calls():
    ids = {new_id("tsk") for _ in range(200)}
    assert len(ids) == 200


def test_new_id_rejects_unknown_prefix():
    with pytest.raises(ValueError):
        new_id("bad")
