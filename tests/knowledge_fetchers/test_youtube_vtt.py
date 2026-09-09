from __future__ import annotations

"""Tests for tinyagentos.knowledge_fetchers.youtube VTT parsing (tsk-t4wzqs).

Covers the three acceptance criteria for the hours-less WebVTT fix:
  * an MM:SS.mmm (hours-less) caption file parses,
  * an unparseable caption file raises or logs rather than being reported as
    an empty transcript,
  * HTML entities are unescaped before indexing.
"""

import logging

from tinyagentos.knowledge_fetchers.youtube import parse_vtt


# WebVTT timestamps with the optional hours component omitted (MM:SS.mmm), the
# form caption tools emit for sub-hour media. The WebVTT spec makes hours
# optional (§4.1 / §4.2), but the old parser required HH:MM:SS and silently
# treated these files as "no captions".
VTT_HOURS_LESS = """\
WEBVTT

00:01.000 --> 00:02.000
First cue

00:02.000 --> 00:03.000
Second cue

00:03.000 --> 00:04.000
Third cue
"""


VTT_ENTITIES = """\
WEBVTT

00:00:01.000 --> 00:00:02.000
it&#39;s &amp; cool
"""


# A blob that is not a WebVTT file at all: no signature, no cues. The old parser
# walked the whole document, matched nothing, and returned [] -- indistinguishable
# from a video that genuinely has no captions.
GARBAGE_VTT = """\
this is not a WebVTT file
no cues, no header, just text
"""


def test_parses_an_hours_less_webvtt_file():
    cues = parse_vtt(VTT_HOURS_LESS)
    assert len(cues) == 3
    assert cues[0]["text"] == "First cue"
    assert cues[0]["start"] == 1.0
    assert cues[0]["end"] == 2.0
    assert cues[2]["start"] == 3.0
    assert cues[2]["text"] == "Third cue"


def test_unparseable_vtt_does_not_silently_succeed(caplog):
    """An unparseable caption file must raise or log, never return [] as success."""
    with caplog.at_level(
        logging.WARNING,
        logger="tinyagentos.knowledge_fetchers.youtube",
    ):
        try:
            result = parse_vtt(GARBAGE_VTT)
        except ValueError:
            return  # raised -- acceptable per DONE-WHEN ("raises or logs")
    # If it returned instead of raising, a warning must have been logged; an
    # empty transcript reported as success is exactly the silent failure we
    # guard against.
    logged = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert result or logged, (
        f"expected a raised error or a logged warning, got {result!r}"
    )


def test_html_entities_are_unescaped_in_cue_text():
    cues = parse_vtt(VTT_ENTITIES)
    assert len(cues) == 1
    assert cues[0]["text"] == "it's & cool"
