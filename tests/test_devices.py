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


# --- 2. model ----------------------------------------------------------------

def test_default_model_has_17_pairs_in_driver_order():
    m = pd.default_model()
    ins = [p for p in m["pairs"] if p["dir"] == "in"]
    outs = [p for p in m["pairs"] if p["dir"] == "out"]
    assert len(ins) == 10 and len(outs) == 7
    assert [p["aux"] for p in ins] == list(range(0, 20, 2))
    assert [p["aux"] for p in outs] == list(range(0, 14, 2))
    assert [p["id"] for p in outs] == ["chusb", "mainaux", "ch1aux", "ch2aux", "ch3aux", "ch4aux", "chsoundpad"]
    assert all(p["enabled"] for p in m["pairs"])
    pd.validate_model(m)


def test_default_labels_carry_coles_names():
    m = pd.default_model()
    label = {(p["dir"], p["id"]): p["label"] for p in m["pairs"]}
    assert label[("in", "ch1")] == "GCS-8 CH 1 (Guitar)"
    assert label[("in", "ch2")] == "GCS-8 CH 2 (Mic)"
    assert label[("out", "ch3aux")] == "GCS-8 CH 3-AUX (Music)"
    assert label[("out", "ch4aux")] == "GCS-8 CH 4-AUX (Voip)"
    assert label[("in", "main")] == "GCS-8 MAIN"


def test_default_model_device_buses_and_clock():
    m = pd.default_model()
    assert m["device"] == {"out": OUT_NODE, "in": IN_NODE}
    assert [b["name"] for b in m["buses"]] == ["SYSTEM", "GAME"]
    assert m["clock_rate"] == 48000


def test_pair_node_names_are_stable_and_ignore_label():
    p = {"id": "ch3aux", "dir": "out", "aux": 8, "label": "anything", "enabled": True}
    assert pd.pair_node_name(p) == "pwctl.gcs8.out.ch3aux"
    assert pd.pair_node_name({**p, "label": "renamed"}) == "pwctl.gcs8.out.ch3aux"


def _with_pair(**changes):
    m = pd.default_model()
    m["pairs"][0] = {**m["pairs"][0], **changes}
    return m


@pytest.mark.parametrize("changes, words", [
    ({"aux": 3}, "even"),
    ({"aux": -2}, "range"),
    ({"aux": 20}, "range"),
    ({"aux": 2}, "overlaps"),
    ({"dir": "sideways"}, "dir"),
    ({"label": ""}, "label"),
    ({"label": "  "}, "label"),
    ({"label": 'say "hi"'}, "label"),
    ({"label": "two\nlines"}, "label"),
    ({"id": "CH 1"}, "id"),
    ({"id": "monitor"}, "duplicate"),
    ({"enabled": "yes"}, "enabled"),
])
def test_validate_rejects_bad_pairs(changes, words):
    with pytest.raises(ValueError, match=words):
        pd.validate_model(_with_pair(**changes))


def test_validate_checks_range_against_hw_channels():
    m = pd.default_model()
    pd.validate_model(m, channels={"out": 14, "in": 20})
    with pytest.raises(ValueError, match="range"):
        pd.validate_model(m, channels={"out": 12, "in": 20})


def test_validate_rejects_duplicate_labels():
    m = pd.default_model()
    m["pairs"][1]["label"] = m["pairs"][0]["label"]
    with pytest.raises(ValueError, match="duplicate label"):
        pd.validate_model(m)


def test_same_label_allowed_across_directions():
    # The driver names CH USB and CH SOUND PAD in both directions; apps list sinks and sources apart.
    m = pd.default_model()
    labels = [p["label"] for p in m["pairs"]]
    assert labels.count("GCS-8 CH USB") == 2
    pd.validate_model(m)


def test_disabled_pair_still_reserves_its_id_and_channels():
    m = _with_pair(enabled=False)
    pd.validate_model(m)
    m["pairs"].append({**m["pairs"][0], "enabled": True})
    with pytest.raises(ValueError, match="duplicate"):
        pd.validate_model(m)


@pytest.mark.parametrize("bus, words", [
    ({"name": "has space", "label": "x"}, "bus name"),
    ({"name": "", "label": "x"}, "bus name"),
    ({"name": "GAME", "label": "x"}, "duplicate"),
    ({"name": "pwctl.gcs8.out.ch1aux", "label": "x"}, "bus name"),
    ({"name": "OBS", "label": ""}, "label"),
])
def test_validate_rejects_bad_buses(bus, words):
    m = pd.default_model()
    m["buses"].append(bus)
    with pytest.raises(ValueError, match=words):
        pd.validate_model(m)


def test_validate_clock_rate():
    m = pd.default_model()
    m["clock_rate"] = None
    pd.validate_model(m)
    m["clock_rate"] = 12345
    with pytest.raises(ValueError, match="clock"):
        pd.validate_model(m)


def test_devices_path_honours_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PWCTL_DEVICES", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert pd.devices_path() == tmp_path / "pwctl" / "devices.json"
    monkeypatch.setenv("PWCTL_DEVICES", str(tmp_path / "x.json"))
    assert pd.devices_path() == tmp_path / "x.json"


def test_load_missing_file_gives_default(tmp_path):
    assert pd.load_model(tmp_path / "nope.json") == pd.default_model()


def test_save_then_load_round_trips(tmp_path):
    m = _with_pair(label="GCS-8 MAIN (unused)", enabled=False)
    path = tmp_path / "pwctl" / "devices.json"
    pd.save_model(path, m)
    assert pd.load_model(path) == m
    assert not list(path.parent.glob("*.tmp"))


def test_save_refuses_invalid_model(tmp_path):
    with pytest.raises(ValueError):
        pd.save_model(tmp_path / "d.json", _with_pair(aux=3))
    assert not (tmp_path / "d.json").exists()


def test_load_rejects_invalid_file(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps(_with_pair(aux=3)))
    with pytest.raises(ValueError):
        pd.load_model(path)
