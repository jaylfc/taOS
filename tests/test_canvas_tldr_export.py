"""The .tldr export is a data-recovery escape hatch, so it must open in a STOCK
tldraw -- one that has never heard of taOS.

Nothing pinned that before, and the export had silently drifted into a file
tldraw refuses to open at all: it emitted the in-memory {schema, store{}} store
snapshot instead of the {tldrawFileFormatVersion, schema, records[]} file
envelope tldraw's parseTldrawJsonFile validates, and typed most shapes as our
own taos-note/taos-link/taos-image utils, which no stock tldraw can render.

These assertions encode the two properties that failure violated. They are
structural on purpose: the real parser lives in the npm package we are removing
(#2132), so a test that imported tldraw would die with the dependency, exactly
when this guarantee starts mattering most.
"""
from __future__ import annotations

import pytest

from tinyagentos.projects.canvas.snapshotter import (
    _TLDRAW_FILE_FORMAT_VERSION,
    _build_tldraw_snapshot,
    _index_key,
)

# Every kind the canvas can hold, plus one it cannot, since an unknown kind must
# still export rather than raise mid-recovery.
ALL_KINDS = [
    "note",
    "text",
    "link",
    "image",
    "mermaid",
    "flowchart",
    "user_shape",
    "kind_from_a_future_version",
]


def _element(id_: str, kind: str, **over):
    el = {
        "id": id_,
        "kind": kind,
        "x": 10.0,
        "y": 20.0,
        "w": 200.0,
        "h": 100.0,
        "rotation": 0.0,
        "z_index": 0,
        "payload": {"text": f"content of {id_}"},
        "author_id": "jay",
        "author_kind": "user",
    }
    el.update(over)
    return el


def test_export_uses_the_file_envelope_not_a_store_snapshot():
    """tldraw rejects anything without these three keys as notATldrawFile."""
    snap = _build_tldraw_snapshot([_element("a", "note")])

    assert snap["tldrawFileFormatVersion"] == _TLDRAW_FILE_FORMAT_VERSION
    assert isinstance(snap["records"], list)
    # The old bug: a "store" dict instead of a "records" list.
    assert "store" not in snap

    # schemaV2 validation requires a sequences dict; without it the file is
    # refused even though the envelope looks right.
    assert snap["schema"]["schemaVersion"] == 2
    assert isinstance(snap["schema"]["sequences"], dict)
    assert snap["schema"]["sequences"]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_no_taos_shape_types_reach_the_export(kind):
    """A stock tldraw has no util for taos-*, so exporting one loses the shape."""
    snap = _build_tldraw_snapshot([_element("a", kind)])
    shapes = [r for r in snap["records"] if r["typeName"] == "shape"]

    assert len(shapes) == 1
    assert not shapes[0]["type"].startswith("taos")
    assert shapes[0]["type"] in {"note", "text", "geo"}


def test_taos_metadata_rides_in_meta_not_props():
    """tldraw validates props per shape type and rejects unknown keys.

    Provenance therefore has to live in meta, which tldraw carries through
    untouched. Putting it in props is what made every geo shape invalid.
    """
    snap = _build_tldraw_snapshot([_element("a", "mermaid")])
    shape = next(r for r in snap["records"] if r["typeName"] == "shape")

    assert shape["meta"]["taos_kind"] == "mermaid"
    assert not any(k.startswith("taos_") for k in shape["props"])


def test_every_element_survives_the_export():
    """The load-bearing rule: a board of N must not come back emptier."""
    elements = [_element(f"e{i}", ALL_KINDS[i % len(ALL_KINDS)]) for i in range(40)]
    snap = _build_tldraw_snapshot(elements)

    shapes = [r for r in snap["records"] if r["typeName"] == "shape"]
    assert len(shapes) == len(elements)
    assert {s["id"] for s in shapes} == {f"shape:e{i}" for i in range(40)}


def test_a_corrupt_tldraw_blob_cannot_reject_the_whole_file():
    """One bad record makes tldraw refuse the ENTIRE file (invalidRecords).

    So user_shape payloads are converted like everything else rather than passed
    through. Passing through would risk costing the user their whole board in
    the one file whose only job is recovery.
    """
    corrupt = _element(
        "u", "user_shape", payload={"tldraw_shape": {"type": "draw", "props": {"nonsense": 1}}}
    )
    shape = next(
        r for r in _build_tldraw_snapshot([corrupt])["records"] if r["typeName"] == "shape"
    )

    assert shape["type"] == "geo"
    assert "nonsense" not in shape["props"]


def test_elements_with_missing_fields_still_export():
    """A recovery path must not raise on the ragged rows it exists to rescue."""
    bare = _element(
        "bare", "note", payload=None, author_id=None, author_kind=None,
        z_index=None, w=0.0, h=0.0, rotation=None,
    )
    shapes = [r for r in _build_tldraw_snapshot([bare])["records"] if r["typeName"] == "shape"]

    assert len(shapes) == 1
    assert shapes[0]["rotation"] == 0


def test_index_keys_are_valid_and_ascending_past_the_first_62():
    """tldraw validates index keys, so "a10" is rejected (a fraction may not end
    in 0). Keys must also sort in z-order, which naive counters break at 62."""
    keys = [_index_key(i) for i in range(200)]

    assert keys == sorted(keys), "index keys must sort into the intended z-order"
    assert len(set(keys)) == len(keys)
    assert keys[:3] == ["a0", "a1", "a2"]
    # The rollover that a naive "a" + str(i) scheme gets wrong.
    assert keys[61] == "az"
    assert keys[62] == "b00"


def test_z_index_drives_export_order():
    elements = [
        _element("top", "note", z_index=5),
        _element("bottom", "note", z_index=1),
    ]
    shapes = [
        r for r in _build_tldraw_snapshot(elements)["records"] if r["typeName"] == "shape"
    ]

    assert [s["id"] for s in shapes] == ["shape:bottom", "shape:top"]


# A recovery export runs precisely on boards whose rows are already damaged, so
# every read has to survive garbage. Both CodeRabbit and Qodo caught that the
# original raised on a non-dict payload: one bad row aborted the export for the
# entire project, losing everything the user was trying to rescue.
HOSTILE_ROWS = [
    {"id": "list-payload", "kind": "note", "payload": ["not", "a", "dict"]},
    {"id": "str-payload", "kind": "note", "payload": "bare string"},
    {"id": "int-payload", "kind": "note", "payload": 42},
    {"id": "no-kind"},
    {"kind": "text"},  # no id
    {"id": "bad-types", "kind": "note", "x": "oops", "y": None,
     "w": True, "h": [], "rotation": "x", "payload": {}},
]


def test_export_survives_every_malformed_row():
    snap = _build_tldraw_snapshot(HOSTILE_ROWS)
    shapes = [r for r in snap["records"] if r["typeName"] == "shape"]

    assert len(shapes) == len(HOSTILE_ROWS), "a damaged row must degrade, not vanish"
    for s in shapes:
        assert isinstance(s["x"], float)
        assert isinstance(s["y"], float)
        assert isinstance(s["rotation"], float)
        assert not s["type"].startswith("taos")


def test_non_dict_payload_does_not_raise():
    """The specific crash both bots found: payload.get on a non-mapping."""
    for payload in (["a"], "text", 42, 3.5, True):
        row = {"id": "x", "kind": "note", "payload": payload}
        shapes = [
            r for r in _build_tldraw_snapshot([row])["records"]
            if r["typeName"] == "shape"
        ]
        assert len(shapes) == 1


def test_a_string_payload_is_used_as_the_label():
    """If the payload is just text, that text is the most useful thing to show."""
    row = {"id": "x", "kind": "note", "payload": "recover me"}
    shape = next(
        r for r in _build_tldraw_snapshot([row])["records"] if r["typeName"] == "shape"
    )
    text = shape["props"]["richText"]["content"][0]["content"][0]["text"]
    assert text == "recover me"


def test_bool_dimensions_do_not_pass_as_numbers():
    """bool is an int subclass, so True would silently become a width of 1.0."""
    from tinyagentos.projects.canvas.snapshotter import _num_or

    assert _num_or(True, 200.0) == 200.0
    assert _num_or(False, 200.0) == 200.0
    assert _num_or(7, 200.0) == 7.0
    assert _num_or("x", 200.0) == 200.0
