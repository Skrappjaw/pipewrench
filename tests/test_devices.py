import json
import shutil
import subprocess
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


# --- 3. generator ------------------------------------------------------------

GOLDEN = FIX / "golden"


def _pair(m, d, pid):
    return next(p for p in m["pairs"] if p["dir"] == d and p["id"] == pid)


def _small_model():
    m = pd.default_model()
    m["pairs"] = [_pair(m, "in", "ch2"), _pair(m, "out", "ch3aux")]
    m["buses"] = [{"name": "SYSTEM", "label": "SYSTEM"}]
    return m


def test_render_output_pair_matches_golden():
    m = pd.default_model()
    assert pd.render_pair(_pair(m, "out", "ch3aux"), m) == (GOLDEN / "pair_out.conf").read_text()


def test_render_input_pair_matches_golden():
    m = pd.default_model()
    assert pd.render_pair(_pair(m, "in", "ch2"), m) == (GOLDEN / "pair_in.conf").read_text()


def test_render_bus_matches_golden():
    assert pd.render_bus({"name": "SYSTEM", "label": "SYSTEM"}) == (GOLDEN / "bus.conf").read_text()


def test_render_dropin_matches_golden():
    assert pd.render_dropin(_small_model()) == (GOLDEN / "small.conf").read_text()


def test_dropin_starts_with_generated_header():
    assert pd.render_dropin(pd.default_model()).startswith("# Generated by pwctl")


def test_dropin_default_has_17_pairs_and_2_buses():
    text = pd.render_dropin(pd.default_model())
    assert text.count("libpipewire-module-loopback") == 17
    assert text.count("support.null-audio-sink") == 2
    for p in pd.default_model()["pairs"]:
        assert f'node.name = "{pd.pair_node_name(p)}"' in text


def test_dropin_leaves_out_disabled_pairs():
    m = _small_model()
    _pair(m, "in", "ch2")["enabled"] = False
    text = pd.render_dropin(m)
    assert "pwctl.gcs8.in.ch2" not in text
    assert "pwctl.gcs8.out.ch3aux" in text


def test_dropin_omits_empty_sections():
    m = _small_model()
    m["pairs"], m["buses"], m["clock_rate"] = [], [], None
    text = pd.render_dropin(m)
    for section in ("context.properties", "context.modules", "context.objects"):
        assert section not in text
    assert text.startswith("# Generated by pwctl")


def test_dropin_relabel_keeps_node_name():
    m = _small_model()
    _pair(m, "out", "ch3aux")["label"] = "Spotify"
    text = pd.render_dropin(m)
    assert 'node.description = "Spotify"' in text
    assert 'node.name = "pwctl.gcs8.out.ch3aux"' in text


def test_dropin_uses_model_device_names():
    m = _small_model()
    m["device"] = {"out": "alsa_output.other.pro-output-0", "in": "alsa_input.other.pro-input-0"}
    text = pd.render_dropin(m)
    assert 'target.object = "alsa_output.other.pro-output-0"' in text
    assert 'target.object = "alsa_input.other.pro-input-0"' in text


def test_dropin_refuses_invalid_model():
    with pytest.raises(ValueError):
        pd.render_dropin(_with_pair(aux=3))


@pytest.mark.skipif(not shutil.which("spa-json-dump"), reason="needs PipeWire's spa-json-dump")
def test_dropin_parses_with_pipewire_spa_json(tmp_path):
    conf = tmp_path / "50-pwctl-devices.conf"
    conf.write_text(pd.render_dropin(pd.default_model()))
    out = subprocess.run(["spa-json-dump", str(conf)], capture_output=True, text=True, check=True).stdout
    parsed = json.loads(out)
    assert parsed["context.properties"] == {"default.clock.rate": 48000}
    mods = parsed["context.modules"]
    assert len(mods) == 17
    mic = next(x["args"] for x in mods if x["args"]["node.description"] == "GCS-8 CH 2 (Mic)")
    assert mic["capture.props"]["audio.position"] == ["AUX6", "AUX7"]
    assert mic["playback.props"]["node.name"] == "pwctl.gcs8.in.ch2"
    assert [o["args"]["node.name"] for o in parsed["context.objects"]] == ["SYSTEM", "GAME"]


# --- 4. apply / rollback -----------------------------------------------------

@pytest.fixture
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_dropin_path_under_xdg(xdg):
    assert pd.dropin_path() == xdg / "pipewire" / "pipewire.conf.d" / "50-pwctl-devices.conf"


def test_diff_against_missing_file_shows_whole_file(xdg):
    text = pd.render_dropin(_small_model())
    diff = pd.diff_dropin(pd.dropin_path(), text)
    assert diff.startswith("--- ")
    assert '+                node.name = "pwctl.gcs8.in.ch2"' in diff


def test_diff_same_text_is_empty(xdg):
    text = pd.render_dropin(_small_model())
    pd.write_dropin(pd.dropin_path(), text)
    assert pd.diff_dropin(pd.dropin_path(), text) == ""


def test_write_creates_dirs_and_no_backup_first_time(xdg):
    path = pd.dropin_path()
    assert pd.write_dropin(path, pd.render_dropin(_small_model())) is True
    assert path.read_text() == pd.render_dropin(_small_model())
    assert not path.with_name(path.name + ".bak").exists()
    assert not list(path.parent.glob("*.tmp"))


def test_write_keeps_previous_as_backup(xdg):
    path = pd.dropin_path()
    first = pd.render_dropin(_small_model())
    pd.write_dropin(path, first)
    m = _small_model()
    _pair(m, "out", "ch3aux")["label"] = "Spotify"
    pd.write_dropin(path, pd.render_dropin(m))
    assert path.with_name(path.name + ".bak").read_text() == first
    assert "Spotify" in path.read_text()


def test_write_unchanged_does_nothing(xdg):
    path = pd.dropin_path()
    text = pd.render_dropin(_small_model())
    pd.write_dropin(path, text)
    assert pd.write_dropin(path, text) is False
    assert not path.with_name(path.name + ".bak").exists()


def test_write_refuses_file_pwctl_did_not_generate(xdg):
    path = pd.dropin_path()
    path.parent.mkdir(parents=True)
    path.write_text("context.modules = [ ]\n")
    with pytest.raises(pd.DevicesError, match="not generated by pwctl"):
        pd.write_dropin(path, pd.render_dropin(_small_model()))
    assert path.read_text() == "context.modules = [ ]\n"


def test_rollback_restores_backup(xdg):
    path = pd.dropin_path()
    first = pd.render_dropin(_small_model())
    pd.write_dropin(path, first)
    pd.write_dropin(path, pd.render_dropin(pd.default_model()))
    assert "restored" in pd.rollback_dropin(path)
    assert path.read_text() == first
    assert not path.with_name(path.name + ".bak").exists()


def test_rollback_without_backup_removes_file(xdg):
    path = pd.dropin_path()
    pd.write_dropin(path, pd.render_dropin(_small_model()))
    assert "removed" in pd.rollback_dropin(path)
    assert not path.exists()


def test_rollback_with_nothing_is_a_no_op(xdg):
    assert "nothing" in pd.rollback_dropin(pd.dropin_path())


def test_rollback_refuses_foreign_file(xdg):
    path = pd.dropin_path()
    path.parent.mkdir(parents=True)
    path.write_text("# someone else's\n")
    with pytest.raises(pd.DevicesError):
        pd.rollback_dropin(path)
    assert path.exists()


def test_restart_command_restarts_pipewire_stack():
    assert pd.RESTART_CMD == ["systemctl", "--user", "restart", "pipewire", "pipewire-pulse", "wireplumber"]


# --- 5. post-apply check -----------------------------------------------------

def _node(name, cls, position):
    return {"type": "PipeWire:Interface:Node", "id": abs(hash(name)) % 10000,
            "info": {"props": {"node.name": name, "media.class": cls, "audio.position": position}}}


def _applied_dump(m, position_style="spa"):
    """A pw-dump as it should look after apply: hardware, then each pair's two nodes, then buses."""
    out = dump()
    for p in m["pairs"]:
        if not p["enabled"]:
            continue
        name = pd.pair_node_name(p)
        aux = [f"AUX{p['aux']}", f"AUX{p['aux'] + 1}"]
        hw_pos = {"spa": f"[ {aux[0]} {aux[1]} ]", "comma": ",".join(aux), "list": aux}[position_style]
        hw_cls = "Stream/Output/Audio" if p["dir"] == "out" else "Stream/Input/Audio"
        out += [_node(name, "Audio/Sink" if p["dir"] == "out" else "Audio/Source", "[ FL FR ]"),
                _node(f"{name}.hw", hw_cls, hw_pos)]
    out += [_node(b["name"], "Audio/Duplex", "FL,FR") for b in m["buses"]]
    return out


@pytest.mark.parametrize("style", ["spa", "comma", "list"])
def test_check_applied_all_present(style):
    m = pd.default_model()
    assert pd.check_applied(m, _applied_dump(m, style)) == []


def test_check_applied_reports_missing_pair_and_bus():
    m = pd.default_model()
    d = [o for o in _applied_dump(m) if _props_name(o) not in ("pwctl.gcs8.out.ch3aux", "GAME")]
    problems = pd.check_applied(m, d)
    assert any("pwctl.gcs8.out.ch3aux" in p and "missing" in p for p in problems)
    assert any("GAME" in p and "missing" in p for p in problems)
    assert len(problems) == 2


def test_check_applied_reports_wrong_channels():
    m = pd.default_model()
    d = _applied_dump(m)
    for o in d:
        if _props_name(o) == "pwctl.gcs8.out.ch3aux.hw":
            o["info"]["props"]["audio.position"] = "[ AUX0 AUX1 ]"
    problems = pd.check_applied(m, d)
    assert problems == ["pwctl.gcs8.out.ch3aux.hw: on AUX0, AUX1, expected AUX8, AUX9"]


def test_check_applied_ignores_disabled_pairs():
    m = pd.default_model()
    _pair(m, "in", "main")["enabled"] = False
    assert pd.check_applied(m, _applied_dump(m)) == []


def test_check_applied_reports_missing_hardware():
    m = pd.default_model()
    d = [o for o in _applied_dump(m) if "BOSS" not in _props_name(o)]
    problems = pd.check_applied(m, d)
    assert problems[0].startswith("GCS-8 not found")


def test_pair_status_marks_present_and_missing():
    m = pd.default_model()
    d = [o for o in _applied_dump(m) if _props_name(o) != "pwctl.gcs8.in.ch2"]
    status = pd.pair_status(m, d)
    assert status["pwctl.gcs8.in.ch2"] is False
    assert status["pwctl.gcs8.out.ch3aux"] is True
    assert len(status) == 17


def _props_name(o):
    return ((o.get("info") or {}).get("props") or {}).get("node.name", "")


# --- 7a. model edits (used by the TUI) ---------------------------------------

def test_rates_match_pwcore_choices():
    import pwcore
    assert pd.RATES == [r for r in pwcore.setting("clock.force-rate").choices if r]


def test_items_group_inputs_outputs_buses():
    m = pd.default_model()
    m["pairs"] = [_pair(m, "out", "ch3aux"), _pair(m, "in", "ch2")]
    assert pd.dev_items(m) == [("pair", "in", "ch2"), ("pair", "out", "ch3aux"), ("bus", "SYSTEM"), ("bus", "GAME")]


def test_set_label_on_pair_and_bus_keeps_node_names():
    m = pd.default_model()
    m2 = pd.set_label(m, ("pair", "out", "ch3aux"), "Spotify")
    assert _pair(m2, "out", "ch3aux")["label"] == "Spotify"
    assert _pair(m, "out", "ch3aux")["label"] == "GCS-8 CH 3-AUX (Music)"  # input untouched
    m3 = pd.set_label(m2, ("bus", "GAME"), "Game audio")
    assert m3["buses"][1] == {"name": "GAME", "label": "Game audio"}


def test_set_label_rejects_bad_text():
    with pytest.raises(ValueError):
        pd.set_label(pd.default_model(), ("pair", "in", "ch2"), 'quote"d')


def test_toggle_pair():
    m = pd.toggle_pair(pd.default_model(), "in", "main")
    assert _pair(m, "in", "main")["enabled"] is False
    assert _pair(pd.toggle_pair(m, "in", "main"), "in", "main")["enabled"] is True


def test_add_and_delete_bus():
    m = pd.add_bus(pd.default_model(), "VOIP_MIC")
    assert m["buses"][-1] == {"name": "VOIP_MIC", "label": "VOIP_MIC"}
    with pytest.raises(ValueError, match="duplicate"):
        pd.add_bus(m, "VOIP_MIC")
    with pytest.raises(ValueError, match="bus name"):
        pd.add_bus(m, "has space")
    m = pd.delete_bus(m, "SYSTEM")
    assert [b["name"] for b in m["buses"]] == ["GAME", "VOIP_MIC"]


def test_edits_on_unknown_items_raise_keyerror():
    m = pd.default_model()
    with pytest.raises(KeyError):
        pd.toggle_pair(m, "in", "nope")
    with pytest.raises(KeyError):
        pd.delete_bus(m, "NOPE")
    with pytest.raises(KeyError):
        pd.set_label(m, ("bus", "NOPE"), "x")


def test_dropin_brackets_balance():
    text = pd.render_dropin(pd.default_model())
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    assert body.count("{") == body.count("}")
    assert body.count("[") == body.count("]")
