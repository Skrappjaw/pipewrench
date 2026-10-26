import json
from pathlib import Path

import pytest

import pwconfig as pc

FIX = Path(__file__).parent / "fixtures"
GOLDEN = FIX / "golden"
CH2_IN = "alsa_input.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_in_GCS8_0_6_7__source"
CH3_OUT = "alsa_output.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_out_GCS8_0_8_9__sink"


@pytest.fixture
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("PIPEWRENCH_DEVICES", raising=False)
    return tmp_path


def cfg(**changes):
    return {**pc.default_config(), **changes}


# --- 2. model v2 ---------------------------------------------------------------

def test_default_config_is_empty_and_generic():
    assert pc.default_config() == {"version": 2, "clock_rate": None, "buses": [], "labels": {}, "hidden": [], "boost": [], "profiles": {}}
    pc.validate_config(pc.default_config())


def test_v1_file_migrates_keeping_buses_and_clock(xdg):
    v1 = {"device": {"out": "x", "in": "y"}, "clock_rate": 48000,
          "pairs": [{"id": "ch2", "dir": "in", "aux": 6, "label": "GCS-8 CH 2 (Mic)", "enabled": True}],
          "buses": [{"name": "SYSTEM", "label": "SYSTEM"}]}
    path = pc.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(v1))
    c = pc.load_config(path)
    assert c == {"version": 2, "clock_rate": 48000, "buses": [{"name": "SYSTEM", "label": "SYSTEM"}],
                 "labels": {}, "hidden": [], "boost": [], "profiles": {}}


def test_missing_file_gives_default(xdg):
    assert pc.load_config(pc.config_path()) == pc.default_config()


def test_save_load_round_trip_atomic(xdg):
    c = cfg(labels={CH2_IN: "GCS-8 CH 2 (Mic)"}, hidden=["x"], buses=[{"name": "OBS", "label": "OBS feed"}])
    pc.save_config(pc.config_path(), c)
    assert pc.load_config(pc.config_path()) == c
    assert not list(pc.config_path().parent.glob("*.tmp"))


def test_config_path_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPEWRENCH_DEVICES", str(tmp_path / "d.json"))
    assert pc.config_path() == tmp_path / "d.json"


@pytest.mark.parametrize("changes, words", [
    ({"clock_rate": 12345}, "clock"),
    ({"labels": {CH2_IN: ""}}, "label"),
    ({"labels": {CH2_IN: 'say "hi"'}}, "label"),
    ({"labels": {CH2_IN: "a\\b"}}, "label"),
    ({"labels": {'bad"name': "x"}}, "node name"),
    ({"hidden": "x"}, "hidden"),
    ({"buses": [{"name": "has space", "label": "x"}]}, "bus name"),
    ({"buses": [{"name": "A", "label": "x"}, {"name": "A", "label": "y"}]}, "duplicate"),
    ({"buses": [{"name": "A", "label": ""}]}, "label"),
    ({"clock_quantum": 100}, "buffer"),
    ({"clock_min_quantum": 0}, "buffer"),
    ({"clock_min_quantum": 1024, "clock_max_quantum": 256}, "min buffer"),
])
def test_validate_rejects(changes, words):
    with pytest.raises(ValueError, match=words):
        pc.validate_config(cfg(**changes))


# --- edits -----------------------------------------------------------------------

def test_set_and_clear_label_on_hardware_node():
    c = pc.set_label(cfg(), CH2_IN, "GCS-8 CH 2 (Mic)")
    assert c["labels"] == {CH2_IN: "GCS-8 CH 2 (Mic)"}
    assert pc.set_label(c, CH2_IN, None)["labels"] == {}
    assert pc.set_label(c, CH2_IN, "  ")["labels"] == {}


def test_set_label_on_a_bus_changes_the_bus_label():
    c = pc.set_label(cfg(buses=[{"name": "OBS", "label": "OBS"}]), "OBS", "OBS feed")
    assert c["buses"] == [{"name": "OBS", "label": "OBS feed"}] and c["labels"] == {}


def test_add_and_delete_bus():
    c = pc.add_bus(cfg(), "GAME")
    assert c["buses"] == [{"name": "GAME", "label": "GAME"}]
    with pytest.raises(ValueError, match="duplicate"):
        pc.add_bus(c, "GAME")
    c = pc.delete_bus(c, "GAME")
    assert c["buses"] == []
    with pytest.raises(KeyError):
        pc.delete_bus(c, "GAME")


def test_toggle_hidden():
    c = pc.toggle_hidden(cfg(), "x")
    assert c["hidden"] == ["x"]
    assert pc.toggle_hidden(c, "x")["hidden"] == []


def test_edits_do_not_mutate_input():
    c = cfg()
    pc.add_bus(c, "A")
    pc.set_label(c, CH2_IN, "x")
    pc.toggle_hidden(c, "x")
    assert c == pc.default_config()


# --- 3. generated files ------------------------------------------------------------

def test_dropin_buses_and_clock_match_golden():
    c = cfg(clock_rate=48000, buses=[{"name": "SYSTEM", "label": "SYSTEM"}])
    assert pc.render_dropin(c) == (GOLDEN / "dropin_v2.conf").read_text()


def test_dropin_writes_saved_clock_settings():
    c = cfg(clock_rate=96000, clock_quantum=128, clock_min_quantum=64, clock_max_quantum=1024)
    text = pc.render_dropin(c)
    assert ("context.properties = {\n    default.clock.rate = 96000\n    default.clock.quantum = 128\n"
            "    default.clock.min-quantum = 64\n    default.clock.max-quantum = 1024\n}\n") in text


def test_saved_clock_keys_survive_load(xdg):
    c = cfg(clock_quantum=256, clock_max_quantum=4096)
    pc.save_config(pc.config_path(), c)
    assert pc.load_config(pc.config_path()) == c


def test_clock_fields_from_live_settings_leave_out_unforced_and_stock():
    live = {"clock.force-rate": 0, "clock.force-quantum": 0, "clock.min-quantum": 32, "clock.max-quantum": 2048}
    assert pc.clock_fields(live) == {"clock_rate": None, "clock_quantum": None,
                                     "clock_min_quantum": None, "clock_max_quantum": None}
    live = {"clock.force-rate": 48000, "clock.force-quantum": 128, "clock.min-quantum": 64, "clock.max-quantum": 1024}
    assert pc.clock_fields(live) == {"clock_rate": 48000, "clock_quantum": 128,
                                     "clock_min_quantum": 64, "clock_max_quantum": 1024}


def test_dropin_empty_config_is_header_only():
    text = pc.render_dropin(cfg())
    assert text.startswith("# Generated by PipeWrench") and "context." not in text


def test_labels_wp04_lua_matches_golden():
    c = cfg(labels={CH3_OUT: "GCS-8 CH 3-AUX (Music)", CH2_IN: "GCS-8 CH 2 (Mic)",
                    "bluez_output.AA_BB.1": "Headphones"})
    assert pc.render_labels(c, (0, 4, 17)) == (GOLDEN / "labels_wp04.lua").read_text()


def test_labels_wp05_conf_matches_golden():
    c = cfg(labels={CH3_OUT: "GCS-8 CH 3-AUX (Music)", CH2_IN: "GCS-8 CH 2 (Mic)",
                    "bluez_output.AA_BB.1": "Headphones"})
    assert pc.render_labels(c, (0, 5, 3)) == (GOLDEN / "labels_wp05.conf").read_text()


def test_labels_path_follows_wireplumber_version(xdg):
    assert pc.labels_path((0, 4, 17)) == xdg / "wireplumber" / "main.lua.d" / "51-pipewrench-labels.lua"
    assert pc.labels_path((0, 5, 0)) == xdg / "wireplumber" / "wireplumber.conf.d" / "51-pipewrench-labels.conf"


def test_wireplumber_version_from_runner():
    run = lambda argv: (0, "wireplumber\nCompiled with libwireplumber 0.4.17\nLinked with libwireplumber 0.4.17\n")
    assert pc.wireplumber_version(run) == (0, 4, 17)
    with pytest.raises(pc.ConfigError):
        pc.wireplumber_version(lambda argv: (1, "nope"))


@pytest.mark.parametrize("version", [(0, 4, 17), (0, 5, 3)])
def test_labels_without_any_is_header_only(version):
    text = pc.render_labels(cfg(), version)
    assert "Generated by PipeWrench" in text.splitlines()[0] and "node.description" not in text


# --- generated-file safety (shared by both files) ---------------------------------

def test_write_keeps_backup_and_refuses_foreign_files(xdg):
    path = xdg / "wireplumber" / "main.lua.d" / "51-pipewrench-labels.lua"
    first = pc.render_labels(cfg(), (0, 4, 17))
    assert pc.write_generated(path, first) is True
    second = pc.render_labels(cfg(labels={CH2_IN: "Mic"}), (0, 4, 17))
    pc.write_generated(path, second)
    assert path.with_name(path.name + ".bak").read_text() == first
    assert pc.write_generated(path, second) is False
    other = xdg / "wireplumber" / "main.lua.d" / "51-other.lua"
    other.write_text("-- mine\n")
    with pytest.raises(pc.ConfigError, match="not generated by PipeWrench"):
        pc.write_generated(other, first)


def test_rollback_restores_or_removes(xdg):
    path = pc.dropin_path()
    pc.write_generated(path, pc.render_dropin(cfg()))
    pc.write_generated(path, pc.render_dropin(cfg(clock_rate=48000)))
    assert "restored" in pc.rollback_generated(path)
    assert "removed" in pc.rollback_generated(path)
    assert "nothing" in pc.rollback_generated(path)


def test_diff_shows_changes(xdg):
    path = pc.dropin_path()
    assert pc.diff_generated(path, pc.render_dropin(cfg())).startswith("--- ")
    pc.write_generated(path, pc.render_dropin(cfg()))
    assert pc.diff_generated(path, pc.render_dropin(cfg())) == ""


def test_restart_commands():
    assert pc.RESTART_PIPEWIRE == ["systemctl", "--user", "restart", "pipewire", "pipewire-pulse", "wireplumber"]
    assert pc.RESTART_WIREPLUMBER == ["systemctl", "--user", "restart", "wireplumber"]


def test_check_applied_reports_missing_buses_and_unshown_labels():
    c = cfg(buses=[{"name": "OBS", "label": "OBS"}], labels={CH2_IN: "Mic", "alsa_gone": "Gone"})
    assert pc.check_applied(c, {"OBS": "OBS", CH2_IN: "Mic"}) == []
    assert pc.check_applied(c, {CH2_IN: "GCS-8 CH 2"}) == ["OBS: bus missing", f"{CH2_IN}: shows 'GCS-8 CH 2', expected 'Mic'"]


def test_boost_is_a_saved_list_of_node_names():
    # "Don't ask again" on the 100% volume popover (Skrappjaw, 2026-10-26); the channel menu locks it again.
    c = pc.set_boost(pc.default_config(), "alsa_output.x", True)
    assert c["boost"] == ["alsa_output.x"] and pc.set_boost(c, "alsa_output.x", True)["boost"] == ["alsa_output.x"]
    assert pc.set_boost(c, "alsa_output.x", False)["boost"] == []
    assert pc.migrate({"version": 2, "hidden": []})["boost"] == []          # older files gain an empty list
    assert pc.migrate({"version": 2, "boost": ["a"]})["boost"] == ["a"]
    with pytest.raises(ValueError, match="boost"):
        pc.validate_config({**pc.default_config(), "boost": "a"})


def test_card_profiles_are_saved_by_card_name():
    # Skrappjaw, 2026-10-26: on/off has to survive restarts, so PipeWrench keeps each card's profile itself.
    c = pc.set_profile(pc.default_config(), "alsa_card.usb-BOSS_GCS-8-01", "off")
    assert c["profiles"] == {"alsa_card.usb-BOSS_GCS-8-01": "off"}
    assert pc.set_profile(c, "alsa_card.usb-BOSS_GCS-8-01", "HiFi")["profiles"] == {"alsa_card.usb-BOSS_GCS-8-01": "HiFi"}
    assert pc.migrate({"version": 2})["profiles"] == {}                    # older files gain an empty map
    assert pc.migrate({"version": 2, "profiles": {"a": "off"}})["profiles"] == {"a": "off"}
    pc.validate_config(c)
    with pytest.raises(ValueError):
        pc.validate_config({**pc.default_config(), "profiles": ["off"]})
    with pytest.raises(ValueError):
        pc.validate_config({**pc.default_config(), "profiles": {"a": 3}})
