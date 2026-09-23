"""The stats panel's agent model: agent and system numbers from ONE clock.

Product owner: "the stats on the stats screen need to correlate between
simulated agent usage and system usage so it looks real".

Before this, the per-agent CPU/RAM was a sine plus random() per request while
the system CPU and memory were the host's own readings -- two unrelated sets of
numbers, one of which jumped on every poll. Now `_stats_model` derives every
agent's activity from time with smooth noise, and the system totals are the
agents' sums plus the OS's own base load. These tests hold it to that:
correlation, continuity between 3 s polls, and busy agents out-working idle
ones -- plus the route actually serving the model, and the panel reconciling it
by key rather than rebuilding.
"""
from __future__ import annotations

import asyncio
import json
import statistics

import pytest

import tinyagentos.routes.auth as auth

from test_lock_screen_repaint import _card, _find, _paint_stats

_T0 = 1_790_000_000.0
_SPECS = [
    ("Personal Assistant", True), ("Social Media Manager", True),
    ("Accountant", True), ("Sales Manager", True),
    ("Customer Service", True), ("Archivist", False), ("Night Owl", False),
]

# The demo helpers read the Settings demo-mode switch from the app's data dir.
# A fresh dir with no switch file is a device that has never flipped it: ON
# exactly when a demo flag is set, which is what these tests were written for.
import tempfile as _tempfile
from pathlib import Path as _Path
from types import SimpleNamespace as _NS

_DEMO_DIR = _Path(_tempfile.mkdtemp())
_DEMO_REQ = _NS(app=_NS(state=_NS(data_dir=_DEMO_DIR)))



def _minute(specs=_SPECS, start=_T0, real_cpu=None, samples=21):
    """A simulated minute of 3 s polls."""
    return [auth._stats_model(start + 3.0 * k, specs, real_cpu)
            for k in range(samples)]


class TestSystemTracksTheAgents:
    @pytest.mark.parametrize("start", [_T0, _T0 + 4321.0, _T0 + 86400.0 * 3])
    def test_system_cpu_covers_and_tracks_the_agent_sum(self, start):
        polls = _minute(start=start)
        system = [p["cpu_percent"] for p in polls]
        agents = [sum(a["cpu_percent"] for a in p["agents"]) for p in polls]
        for s, a in zip(system, agents):
            assert s >= a - 0.2, (s, a)          # rounding only
        r = statistics.correlation(system, agents)
        assert r > 0.8, r

    def test_system_cpu_is_base_plus_the_agents(self):
        for p in _minute():
            want = p["system"]["base_cpu_percent"] + p["system"]["agents_cpu_percent"]
            assert abs(p["cpu_percent"] - min(97.0, want)) <= 0.2, p

    def test_memory_and_throughput_are_the_agents_sums(self):
        p = auth._stats_model(_T0, _SPECS)
        agents_kb = sum(a["ram_mb"] for a in p["agents"]) * 1024
        assert abs(p["memory"]["agents_kb"] - agents_kb) <= len(_SPECS) * 1024
        assert p["memory"]["used_kb"] > p["memory"]["agents_kb"]
        tok = sum(a["tokens_per_s"] for a in p["agents"])
        assert abs(p["system"]["tokens_per_s"] - tok) <= 0.1 * len(_SPECS)

    def test_the_numbers_suit_an_8gb_778g_phone(self):
        for p in _minute():
            assert 5 <= p["cpu_percent"] <= 97
            assert p["memory"]["total_kb"] < 8 * 1024 * 1024
            assert 25 <= p["memory"]["percent"] <= 90
            assert 30 <= p["system"]["temp_c"] <= 60
            assert 0.5 <= p["system"]["power_w"] <= 9

    def test_a_real_reading_shows_through_as_the_base(self):
        auth._SIM_BASE_EMA.clear()
        try:
            quiet = auth._stats_model(_T0, _SPECS, 2.0)["system"]["base_cpu_percent"]
            auth._SIM_BASE_EMA.clear()
            busy = auth._stats_model(_T0, _SPECS, 40.0)["system"]["base_cpu_percent"]
        finally:
            auth._SIM_BASE_EMA.clear()
        assert busy > quiet + 10, (quiet, busy)

    def test_the_history_is_the_model_itself(self):
        """The sparkline's last bar is the number printed above it."""
        p = auth._stats_model(_T0, _SPECS)
        h = p["history"]
        assert len(h["cpu"]) == len(h["agents_cpu"]) == 20
        assert abs(h["cpu"][-1] - p["cpu_percent"]) <= 0.1
        assert abs(h["agents_cpu"][-1] - p["system"]["agents_cpu_percent"]) <= 0.1
        earlier = auth._stats_model(_T0 - 3.0, _SPECS)
        assert abs(h["cpu"][-2] - earlier["cpu_percent"]) <= 0.1


class TestNoJumpsBetweenPolls:
    def test_consecutive_polls_are_close(self):
        polls = _minute(samples=200)
        cpu = [p["cpu_percent"] for p in polls]
        assert max(abs(a - b) for a, b in zip(cpu, cpu[1:])) <= 8.0
        for i in range(len(_SPECS)):
            one = [p["agents"][i]["cpu_percent"] for p in polls]
            assert max(abs(a - b) for a, b in zip(one, one[1:])) <= 4.0
            ram = [p["agents"][i]["ram_mb"] for p in polls]
            assert max(abs(a - b) for a, b in zip(ram, ram[1:])) <= 60
        temp = [p["system"]["temp_c"] for p in polls]
        assert max(abs(a - b) for a, b in zip(temp, temp[1:])) <= 1.5

    def test_the_same_moment_reads_the_same(self):
        """Derived from time, not from random() per request."""
        assert auth._stats_model(_T0, _SPECS) == auth._stats_model(_T0, _SPECS)

    def test_it_is_not_frozen_either(self):
        polls = _minute()
        assert len({p["cpu_percent"] for p in polls}) > 10


class TestBusyOutworksIdle:
    def test_every_busy_agent_averages_above_every_idle_one(self):
        polls = _minute(samples=100)
        mean = {name: statistics.mean(p["agents"][i]["cpu_percent"] for p in polls)
                for i, (name, _b) in enumerate(_SPECS)}
        tokens = {name: statistics.mean(p["agents"][i]["tokens_per_s"] for p in polls)
                  for i, (name, _b) in enumerate(_SPECS)}
        busy = [n for n, b in _SPECS if b]
        idle = [n for n, b in _SPECS if not b]
        assert min(mean[n] for n in busy) > max(mean[n] for n in idle), mean
        assert min(tokens[n] for n in busy) > max(tokens[n] for n in idle), tokens

    def test_busy_is_the_islands_answer(self, monkeypatch):
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS",
                           "A:hermes:Drafting replies,B:openclaw:idle,C,D:x:Stopped")
        assert auth._demo_agent_specs(_DEMO_REQ) == [
            ("A", True), ("B", False), ("C", True), ("D", False)]


class TestTheRouteServesTheModel:
    def _get(self, monkeypatch, demo: str):
        class _Req:
            pass
        monkeypatch.setattr(auth, "_request_is_console", lambda r: True)
        monkeypatch.setattr(auth, "_read_cpu_percent", lambda: 12.0)
        monkeypatch.setattr(auth, "_read_memory", lambda: {
            "total_kb": 7_900_000, "used_kb": 2_300_000, "percent": 29.1})
        monkeypatch.setattr(auth, "_read_gpu", lambda: None)
        monkeypatch.setattr(auth, "_read_dsp_states", lambda: [])
        if demo:
            monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", demo)
        else:
            monkeypatch.delenv("TAOS_LOCK_DEMO_AGENTS", raising=False)
        resp = asyncio.run(auth.lock_stats(_DEMO_REQ))
        return json.loads(bytes(resp.body).decode())

    def test_a_non_console_request_is_refused(self, monkeypatch):
        monkeypatch.setattr(auth, "_request_is_console", lambda r: False)
        assert asyncio.run(auth.lock_stats(object())).status_code == 403

    def test_with_demo_agents_the_totals_come_from_the_model(self, monkeypatch):
        got = self._get(monkeypatch, "A:hermes:Working,B:openclaw:idle")
        agents = sum(a["cpu_percent"] for a in got["agents"])
        assert got["cpu_percent"] >= agents
        assert got["system"]["agents_cpu_percent"] == pytest.approx(agents, abs=0.2)
        assert got["memory"]["agents_kb"] > 0
        assert [a["busy"] for a in got["agents"]] == [True, False]

    def test_without_demo_agents_the_real_readings_stand(self, monkeypatch):
        got = self._get(monkeypatch, "")
        assert got["cpu_percent"] == 12.0
        assert got["memory"]["used_kb"] == 2_300_000
        for key in ("agents", "system", "history"):
            assert key not in got


def _demo_reading(t: float) -> dict:
    p = auth._stats_model(t, _SPECS)
    p["cpu_cores"] = 8
    return p


class TestThePanelReconcilesTheModel:
    def test_a_poll_keeps_every_node(self):
        before, after = _paint_stats([_demo_reading(_T0), _demo_reading(_T0 + 3)])["snapshots"]
        assert _card(after)["id"] == _card(before)["id"]
        for part in ("cpu", "cpu-split", "cpu-spark", "memory", "flow", "thermal"):
            assert _find(_card(after)["kids"], part)["id"] == \
                _find(_card(before)["kids"], part)["id"], part
        spark_b = _find(_card(before)["kids"], "cpu-spark")["kids"]
        spark_a = _find(_card(after)["kids"], "cpu-spark")["kids"]
        assert len(spark_a) == 20
        assert [c["id"] for c in spark_a] == [c["id"] for c in spark_b]
        agents_b, agents_a = _find(before, "agents"), _find(after, "agents")
        assert agents_a["id"] == agents_b["id"]
        assert [k["id"] for k in agents_a["kids"]] == [k["id"] for k in agents_b["kids"]]

    def test_the_agent_bars_are_shares_of_the_system_cpu(self):
        (snap,) = _paint_stats([_demo_reading(_T0)])["snapshots"]
        p = _demo_reading(_T0)
        rows = {k["part"]: k for k in _find(snap, "agents")["kids"]}
        total = 0.0
        for a in p["agents"]:
            width = rows["agent-" + a["name"]]["kids"][1]["kids"][0]["width"]
            total += float(width.rstrip("%"))
        share = p["system"]["agents_cpu_percent"] * 100 / p["cpu_percent"]
        assert total == pytest.approx(share, abs=1.0)

    def test_the_split_caption_names_both_parts(self):
        (snap,) = _paint_stats([_demo_reading(_T0)])["snapshots"]
        p = _demo_reading(_T0)
        text = _find(_card(snap)["kids"], "cpu-split")["text"]
        assert "Agents %d%%" % round(p["system"]["agents_cpu_percent"]) in text
        assert "System %d%%" % round(p["system"]["base_cpu_percent"]) in text
