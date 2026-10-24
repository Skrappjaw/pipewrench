import io
import json
import wave
from pathlib import Path

import pytest

import pwconfig as pcfg
import pwctl

FIX = Path(__file__).parent / "fixtures"
CH2_IN = "alsa_input.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_in_GCS8_0_6_7__source"
CH3_OUT = "alsa_output.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_out_GCS8_0_8_9__sink"
WEBCAM = "alsa_card.usb-046d_HD_Pro_Webcam_C920_0123ABCD-02"


def base_dump():
    return json.loads((FIX / "pwdump_inventory.json").read_text())


def relabel(dump, name, label):
    for o in dump:
        p = (o.get("info") or {}).get("props") or {}
        if p.get("node.name") == name:
            p["node.description"] = label
    return dump


def add_bus_node(dump, name, label):
    dump.append({"id": 9000 + len(dump), "type": "PipeWire:Interface:Node", "info": {"state": "suspended", "props": {
        "node.name": name, "node.description": label, "media.class": "Audio/Duplex",
        "factory.name": "support.null-audio-sink"}, "params": {}}})
    return dump


class Runner:
    """pw-dump from the fixture (or after_restart once a restart ran); everything else is recorded."""

    def __init__(self, dump=None, after_restart=None, fail=()):
        self.calls = []
        self.dump = dump if dump is not None else base_dump()
        self.after_restart = after_restart
        self.fail = fail

    def __call__(self, argv):
        self.calls.append(argv)
        if argv == ["pw-dump"]:
            return 0, json.dumps(self.dump)
        if argv == pcfg.VERSION_CMD:
            return 0, "wireplumber\nCompiled with libwireplumber 0.4.17\n"
        if argv[0] in self.fail:
            return 1, f"{argv[0]} failed"
        if argv[:2] == ["systemctl", "--user"] and self.after_restart is not None:
            self.dump = self.after_restart
        return 0, ""

    def ran(self, prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("PWCTL_DEVICES", raising=False)
    monkeypatch.setattr(pwctl, "SETTLE", lambda: None)
    return tmp_path


def run(*argv, runner=None, answer="n"):
    runner = runner or Runner()
    out, err, asked = io.StringIO(), io.StringIO(), []

    def ask(q):
        asked.append(q)
        return answer

    rc = pwctl.main(["devices", *argv], runner=runner, out=out, err=err, ask=ask)
    return rc, out.getvalue(), err.getvalue(), runner, asked


def config():
    return pcfg.load_config(pcfg.config_path())


# --- list -----------------------------------------------------------------------

def test_list_groups_cards_then_virtual_with_defaults():
    rc, out, *_ = run("list")
    assert rc == 0
    assert out.index("GCS-8 · profile HiFi") < out.index("GCS-8 CH 2 ") < out.index("Virtual") < out.index("SYSTEM")
    stars = [line for line in out.splitlines() if line.strip().startswith("★")]
    assert len(stars) == 2 and any("MAIN-AUX" in s for s in stars) and any("GCS-8 CH 2 " in s for s in stars)
    mic = next(line for line in out.splitlines() if CH2_IN in line)
    assert "running" in mic and "F32P 2ch 48000" in mic and "vol 100%" in mic and " in " in mic
    assert "vol 40%" in next(line for line in out.splitlines() if CH3_OUT in line)


def test_list_is_default_subcommand():
    assert run()[1] == run("list")[1]


def test_list_hidden_and_monitors_flags():
    pcfg.save_config(pcfg.config_path(), {**pcfg.default_config(), "hidden": [CH2_IN]})
    assert CH2_IN not in run("list")[1]
    out = run("list", "--hidden", "--monitors")[1]
    assert "hidden" in next(line for line in out.splitlines() if CH2_IN in line)
    assert "GCS-8 CH 3-AUX (monitor)" in out


def test_list_marks_pending_label():
    pcfg.save_config(pcfg.config_path(), {**pcfg.default_config(), "labels": {CH2_IN: "GCS-8 CH 2 (Mic)"}})
    out = run("list")[1]
    assert "label → GCS-8 CH 2 (Mic)" in next(line for line in out.splitlines() if CH2_IN in line)


def test_list_bus_tags_pending_clash_and_owned():
    pcfg.save_config(pcfg.config_path(), {**pcfg.default_config(),
                                          "buses": [{"name": "SYSTEM", "label": "SYSTEM"}, {"name": "OBS", "label": "OBS"}]})
    out = run("list")[1]
    system = next(line for line in out.splitlines() if line.rstrip().split("  [")[0].endswith(" SYSTEM"))
    assert "made outside pwctl" in system and "[pwctl" not in system
    obs = next(line for line in out.splitlines() if "OBS" in line)
    assert "pending" in obs and "apply" in obs
    # once pwctl's drop-in defines SYSTEM, the live SYSTEM is pwctl's
    pcfg.write_generated(pcfg.dropin_path(), pcfg.render_dropin({**pcfg.default_config(),
                                                                 "buses": [{"name": "SYSTEM", "label": "SYSTEM"}]}))
    out = run("list")[1]
    system = next(line for line in out.splitlines() if line.rstrip().split("  [")[0].endswith(" SYSTEM"))
    assert "[pwctl]" in system


def test_list_columns_stay_aligned_with_long_labels():
    out = run("list")[1]
    rows = [line for line in out.splitlines() if line.startswith("  ") and " vol " in line]
    assert len({line.index(" vol ") for line in rows}) == 1
    assert any("…" in line for line in rows)  # "DualShock 4 [CUH-ZCT2x] Analog Stereo" is cut


def test_list_without_pipewire_fails_clearly():
    def missing(argv):
        raise FileNotFoundError(argv[0])
    rc, _, err, *_ = run("list", runner=missing)
    assert rc == 1 and "pw-dump" in err


# --- live actions --------------------------------------------------------------------

def test_default_volume_mute_run_wpctl():
    for args, cmd in ((("default", CH2_IN), ["wpctl", "set-default", "96"]),
                      (("volume", "96", "+5%"), ["wpctl", "set-volume", "96", "5%+"]),
                      (("mute", "GCS-8 CH 2"), ["wpctl", "set-mute", "96", "toggle"])):
        rc, _, err, r, _ = run(*args)
        assert rc == 0, err
        assert r.ran(["wpctl"]) == [cmd]


@pytest.mark.parametrize("args, code", [(("default", "nope"), 2), (("default", "GCS-8 CH USB"), 2),
                                        (("volume", "96", "loud"), 2), (("volume", "96"), 2)])
def test_bad_device_or_value_exit_2(args, code):
    rc, _, err, r, _ = run(*args)
    assert rc == code and err and r.ran(["wpctl"]) == []


def test_wpctl_failure_exit_1():
    rc, _, err, *_ = run("mute", "96", runner=Runner(fail=("wpctl",)))
    assert rc == 1 and "wpctl failed" in err


def test_profile_asks_then_switches():
    rc, out, _, r, asked = run("profile", "GCS-8", "pro-audio", answer="n")
    assert rc == 1 and r.ran(["wpctl"]) == [] and "GCS-8" in asked[0]
    rc, *_, r, _ = run("profile", "GCS-8", "pro-audio", answer="y")
    assert rc == 0 and r.ran(["wpctl"]) == [["wpctl", "set-profile", "95", "3"]]
    assert run("profile", "GCS-8", "surround", answer="y")[0] == 2
    assert run("profile", "nope", "HiFi", answer="y")[0] == 2


def test_test_plays_noise_on_an_output():
    rc, out, _, r, _ = run("test", CH3_OUT)
    assert rc == 0 and r.ran(["pw-cat"])[0][:4] == ["pw-cat", "--playback", "--target", CH3_OUT]
    assert run("test", CH2_IN)[0] == 2  # an input


def test_noise_wav_is_short_quiet_stereo(tmp_path):
    path = tmp_path / "n.wav"
    pwctl.write_noise(path)
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 2 and w.getframerate() == 48000 and 0.5 <= w.getnframes() / 48000 <= 2


# --- config edits (saved, not applied) -------------------------------------------------

def test_label_set_and_clear():
    rc, out, *_ = run("label", "96", "GCS-8 CH 2 (Mic)")
    assert rc == 0 and "apply" in out and config()["labels"] == {CH2_IN: "GCS-8 CH 2 (Mic)"}
    run("label", "96", "--clear")
    assert config()["labels"] == {}
    assert run("label", "96", 'bad "quote"')[0] == 2


def test_hide_unhide_rows_and_cards():
    run("hide", CH2_IN)
    run("hide", "HD Pro Webcam C920")
    assert config()["hidden"] == [CH2_IN, WEBCAM]
    run("unhide", CH2_IN)
    assert config()["hidden"] == [WEBCAM]
    assert run("hide", "nope")[0] == 2


def test_bus_add_delete():
    assert run("bus", "add", "OBS", "OBS feed")[0] == 0
    assert config()["buses"] == [{"name": "OBS", "label": "OBS feed"}]
    assert run("bus", "add", "OBS")[0] == 2
    assert run("bus", "delete", "OBS")[0] == 0 and config()["buses"] == []
    assert run("bus", "delete", "OBS")[0] == 2


def test_label_on_a_pwctl_bus_relabels_it():
    run("bus", "add", "OBS")
    run("label", "OBS", "OBS feed")
    assert config()["buses"] == [{"name": "OBS", "label": "OBS feed"}]


# --- preview / apply / rollback ----------------------------------------------------------

def test_preview_and_apply_with_nothing_to_do():
    assert "up to date" in run("preview")[1]
    rc, out, _, r, asked = run("apply", answer="y")
    assert rc == 0 and "up to date" in out and asked == [] and r.ran(["systemctl"]) == []


def test_apply_labels_only_restarts_wireplumber_and_checks():
    run("label", "96", "GCS-8 CH 2 (Mic)")
    after = relabel(base_dump(), CH2_IN, "GCS-8 CH 2 (Mic)")
    rc, out, err, r, asked = run("apply", answer="y", runner=Runner(after_restart=after))
    assert rc == 0, err
    assert "WirePlumber" in asked[0] and r.ran(["systemctl"]) == [pcfg.RESTART_WIREPLUMBER]
    lua = pcfg.labels_path((0, 4, 17)).read_text()
    assert CH2_IN in lua and "GCS-8 CH 2 (Mic)" in lua
    assert not pcfg.dropin_path().exists()
    assert "applied" in out


def test_apply_buses_restarts_pipewire_and_reports_missing():
    run("bus", "add", "OBS")
    rc, _, err, r, asked = run("apply", answer="y")  # the dump never gains OBS
    assert r.ran(["systemctl"]) == [pcfg.RESTART_PIPEWIRE] and "PipeWire" in asked[0]
    assert rc == 1 and "OBS" in err and "rollback" in err
    rc, _, err, r, _ = run("apply", answer="y", runner=Runner(after_restart=add_bus_node(base_dump(), "OBS", "OBS")))
    assert "up to date" in run("preview")[1]


def test_apply_declined_writes_nothing():
    run("label", "96", "Mic")
    rc, out, _, r, _ = run("apply", answer="n")
    assert rc == 1 and "nothing written" in out and r.ran(["systemctl"]) == []
    assert not pcfg.labels_path((0, 4, 17)).exists()


def test_apply_warns_about_bus_already_made_outside_pwctl():
    run("bus", "add", "SYSTEM")  # start_audio.sh's SYSTEM is in the fixture
    rc, _, err, _, asked = run("apply", answer="n")
    assert "SYSTEM" in err and "already exists" in err


def test_apply_refuses_foreign_file():
    path = pcfg.labels_path((0, 4, 17))
    path.parent.mkdir(parents=True)
    path.write_text("-- someone else's rules\n")
    run("label", "96", "Mic")
    rc, _, err, r, _ = run("apply", answer="y")
    assert rc == 1 and "not generated by pwctl" in err and r.ran(["systemctl"]) == []


def test_rollback_restores_and_restarts():
    run("label", "96", "Mic")
    run("apply", answer="y")
    rc, out, _, r, _ = run("rollback", answer="y")
    assert rc == 0 and "removed" in out and r.ran(["systemctl"]) == [pcfg.RESTART_WIREPLUMBER]
    assert not pcfg.labels_path((0, 4, 17)).exists()
    rc, out, _, r, asked = run("rollback", answer="y")
    assert rc == 0 and "nothing" in out and asked == []


def test_unknown_subcommand_exit_2():
    rc, _, err, *_ = run("frobnicate")
    assert rc == 2 and "usage" in err
