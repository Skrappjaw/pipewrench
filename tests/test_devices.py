import json
from pathlib import Path

import pytest

import pwdevices as pd

FIX = Path(__file__).parent / "fixtures"
OUT_NODE = "alsa_output.usb-BOSS_GCS-8-01.pro-output-0"
IN_NODE = "alsa_input.usb-BOSS_GCS-8-01.pro-input-0"


def dump():
    return json.loads((FIX / "pwdump_gcs8.json").read_text())


# --- 1. discovery ------------------------------------------------------------

def test_find_hw_reads_gcs8_pro_nodes():
    hw = pd.find_hw(dump())
    assert hw["out"].name == OUT_NODE
    assert hw["out"].channels == 14
    assert hw["out"].positions == [f"AUX{i}" for i in range(14)]
    assert hw["in"].name == IN_NODE
    assert hw["in"].channels == 20
    assert hw["in"].positions[-1] == "AUX19"


def test_find_hw_missing_device_gives_none():
    nodes = [o for o in dump() if "BOSS" not in json.dumps(o)]
    assert pd.find_hw(nodes) == {"out": None, "in": None}


def test_find_hw_ignores_non_pro_profiles():
    other = {"type": "PipeWire:Interface:Node", "id": 9, "info": {"props": {
        "node.name": "alsa_output.usb-BOSS_GCS-8-01.analog-stereo", "media.class": "Audio/Sink",
        "audio.channels": 2, "audio.position": "FL,FR"}}}
    assert pd.find_hw([other])["out"] is None


def test_find_hw_tolerates_missing_info():
    junk = [{"type": "PipeWire:Interface:Node", "id": 1}, {"type": "PipeWire:Interface:Node", "id": 2, "info": None}]
    assert pd.find_hw(junk + dump())["out"].channels == 14


def test_read_dump_uses_runner():
    calls = []

    def run(argv):
        calls.append(argv)
        return 0, json.dumps(dump())

    assert len(pd.read_dump(run)) == 50
    assert calls == [["pw-dump"]]


def test_read_dump_error_raises():
    with pytest.raises(pd.DevicesError):
        pd.read_dump(lambda argv: (1, "connection refused"))
