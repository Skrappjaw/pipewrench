"""Opt-in checks against the real PipeWire on this machine: PIPEWRENCH_LIVE=1 pytest tests/test_live.py

Writes only the values already set, so the audio state never changes.
"""
import os

import pytest

import pwcore as pc
import pipewrench

pytestmark = pytest.mark.skipif(os.environ.get("PIPEWRENCH_LIVE") != "1", reason="set PIPEWRENCH_LIVE=1")


def test_read_real_settings():
    s = pc.read_settings(pipewrench.default_runner)
    for k in ("clock.force-rate", "clock.force-quantum", "clock.min-quantum", "clock.max-quantum"):
        assert isinstance(s.get(k), int)


def test_noop_write_round_trip():
    before = pc.read_settings(pipewrench.default_runner)
    for key in ("clock.force-quantum", "clock.force-rate"):
        rc, out = pipewrench.default_runner(pc.set_cmd(key, before[key]))
        assert rc == 0, out
    after = pc.read_settings(pipewrench.default_runner)
    assert {k: after[k] for k in before} == before


def test_pw_top_stream_parses():
    blocks = []
    p = pc.TopParser()
    for line in pipewrench.pwtop_lines(iterations=2):
        b = p.feed(line)
        if b is not None:
            blocks.append(b)
    blocks.append(p.flush())
    assert blocks and any(len(b) for b in blocks)
