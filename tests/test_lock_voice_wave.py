"""The voice sheet: an emulated live-input waveform, and no microphone.

Product owner: "we need to remove any demo text, like the voice note window
says microphone access refused etc, this should be animated waveform emulating
voice input".

Two properties. The sheet must never ask for the microphone -- the lock screen
renders before sign-in, so a getUserMedia call here was a live mic one tap away
from whoever held the phone. And the waveform must look like someone talking
while the sheet is recording and lie flat when it is not; the envelope is a pure
function of time, so it runs under node without a canvas.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

import tinyagentos.routes.auth as auth

from test_lock_screen_gestures import _function
from test_lock_screen_repaint import _var


def _code(src: str) -> str:
    """The source without its // comments, which legitimately NAME the API."""
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))


class TestNoMicrophone:
    def test_the_voice_sheet_code_path_never_requests_the_microphone(self):
        js = auth._LOCK_SCREEN_SCRIPT
        path = "\n".join(_function(n) for n in (
            "openVoice", "voiceTick", "stopVoice", "drawVoiceWave", "drawWave"))
        for forbidden in ("getUserMedia", "mediaDevices", "MediaRecorder",
                          "AudioContext", "SpeechRecognition", "createAnalyser"):
            assert forbidden not in _code(path), forbidden
        # And nowhere else on the page either: the lock screen is pre-auth.
        for forbidden in (".getUserMedia(", "navigator.mediaDevices",
                          "new MediaRecorder(", "SpeechRecognition"):
            assert forbidden not in _code(js), forbidden

    def test_no_permission_or_error_wording_survives(self):
        js = _code(auth._LOCK_SCREEN_SCRIPT)
        for leak in ("refused", "Not available", "Dictation unavailable",
                     "no microphone", "Type instead", "(demo)"):
            assert leak not in js, leak


def _run(script: str) -> dict:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the runner image
        pytest.fail("node is required to execute the voice envelope")
    done = subprocess.run([node, "-e", script], capture_output=True, text=True,
                          timeout=30, env=dict(os.environ))
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


def _source() -> str:
    return "\n".join([
        _var("VOICE_LEAD_MS"), _var("VOICE_ROOM"),
        _function("voiceHash"), _function("voiceWord"),
        _function("voiceEnvelope"), _function("voiceWordsDone"),
    ])


def _samples(recording: bool, seed: int = 3, words: int = 10,
             ms: int = 4000, step: int = 10) -> list[float]:
    return _run(_source() + """
var out = [];
for (var t = 0; t <= %d; t += %d) out.push(voiceEnvelope(t, %s, %d, %d));
process.stdout.write(JSON.stringify(out));
""" % (ms, step, "true" if recording else "false", seed, words))


class TestTheEmulatedEnvelope:
    def test_recording_is_not_flat(self):
        s = _samples(True)
        mean = sum(s) / len(s)
        sd = (sum((x - mean) ** 2 for x in s) / len(s)) ** 0.5
        assert max(s) > 0.45, max(s)
        assert sd > 0.12, sd

    def test_idle_is_flat(self):
        assert set(_samples(False)) == {0}

    def test_there_are_pauses_between_words(self):
        """Speech, not a tone: stretches at the room floor between bursts."""
        s = _samples(True)
        quiet = [x <= 0.03 for x in s]
        runs, cur = [], 0
        for q in quiet[40:]:           # past the lead-in breath
            if q:
                cur += 1
            elif cur:
                runs.append(cur)
                cur = 0
        assert len([r for r in runs if r * 10 >= 70]) >= 4, runs

    def test_syllables_arrive_at_speech_rate(self):
        """Local maxima inside words: roughly 4-6 per second of voiced time."""
        s = _samples(True, ms=8000, words=40)
        voiced = sum(1 for x in s if x > 0.05) * 10 / 1000.0
        peaks = sum(1 for i in range(1, len(s) - 1)
                    if s[i] > 0.12 and s[i] >= s[i - 1] and s[i] > s[i + 1])
        rate = peaks / voiced
        assert 3.0 <= rate <= 8.0, (peaks, voiced, rate)

    def test_it_moves_smoothly(self):
        """No frame-to-frame snaps at 60 fps: the decay is smooth, not a cut."""
        s = _samples(True, step=16)
        assert max(abs(a - b) for a, b in zip(s, s[1:])) < 0.3

    def test_the_transcript_lands_with_the_words(self):
        got = _run(_source() + """
var out = [];
for (var t = 0; t <= 12000; t += 250) out.push(voiceWordsDone(t, 5, 8));
process.stdout.write(JSON.stringify(out));
""")
        assert got[0] == 0
        assert got == sorted(got), "words are never unsaid"
        assert got[-1] == 8
        assert len(set(got)) > 5, "words arrive one at a time"
