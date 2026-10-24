import json

import pytest

import pwcore as pc

CUR = {"clock.force-rate": 48000, "clock.force-quantum": 128, "clock.min-quantum": 32,
       "clock.max-quantum": 2048, "log.level": 2, "clock.allowed-rates": [48000]}


def make(name="tracking", label="Low-latency tracking", **values):
    values = values or {"clock.force-rate": 48000, "clock.force-quantum": 64}
    return {"name": name, "label": label, "values": values}


def test_presets_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PWCTL_PRESETS", str(tmp_path / "p.json"))
    assert pc.presets_path() == tmp_path / "p.json"


def test_presets_path_default_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("PWCTL_PRESETS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert pc.presets_path() == tmp_path / "pwctl" / "presets.json"


def test_load_missing_file_is_empty(tmp_path):
    assert pc.load_presets(tmp_path / "nope.json") == []


def test_save_load_round_trip_and_atomic(tmp_path):
    path = tmp_path / "sub" / "presets.json"
    presets = pc.add_preset([], make())
    pc.save_presets(path, presets)
    assert pc.load_presets(path) == presets
    assert json.loads(path.read_text())["presets"][0]["name"] == "tracking"
    assert [p.name for p in path.parent.iterdir()] == ["presets.json"]  # no tmp file left behind


def test_add_rejects_duplicate_name():
    presets = pc.add_preset([], make())
    with pytest.raises(ValueError):
        pc.add_preset(presets, make(label="other"))


@pytest.mark.parametrize("bad", [
    make(name=""), make(name="has space"), make(name="x" * 33),
    {"name": "a", "label": "b", "values": {}},
    {"name": "a", "label": "b", "values": {"clock.bogus": 1}},
    {"name": "a", "label": "b", "values": {"clock.force-rate": "fast"}},
    {"name": "a", "label": "b", "values": {"clock.min-quantum": 4096, "clock.max-quantum": 64}},
])
def test_validate_preset_rejects(bad):
    with pytest.raises(ValueError):
        pc.validate_preset(bad)


def test_delete_and_relabel():
    presets = pc.add_preset(pc.add_preset([], make()), make(name="mix", label="Mixing"))
    presets = pc.relabel_preset(presets, "mix", "Mixdown")
    assert pc.find_preset(presets, "mix")["label"] == "Mixdown"
    presets = pc.delete_preset(presets, "tracking")
    assert [p["name"] for p in presets] == ["mix"]
    with pytest.raises(KeyError):
        pc.delete_preset(presets, "tracking")


def test_preset_cmds_order_and_only_its_keys():
    p = make(**{"clock.force-quantum": 4096, "clock.min-quantum": 4096, "clock.max-quantum": 8192})
    cmds = pc.preset_cmds(p, CUR)
    assert [c[4] for c in cmds] == ["clock.max-quantum", "clock.min-quantum", "clock.force-quantum"]
    assert cmds[0] == pc.set_cmd("clock.max-quantum", 8192)


def test_capture_preset_snapshots_live_values():
    p = pc.capture_preset(CUR, "now", "Current setup")
    assert p == {"name": "now", "label": "Current setup",
                 "values": {"clock.force-rate": 48000, "clock.force-quantum": 128,
                            "clock.min-quantum": 32, "clock.max-quantum": 2048}}
    pc.validate_preset(p)


def test_summarize_preset():
    assert pc.summarize_preset(make()) == "rate 48000 · buf 64"
