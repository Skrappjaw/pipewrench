import io
import json
import wave
from pathlib import Path

import pytest

import pwctl
import pwdevices as pd

FIX = Path(__file__).parent / "fixtures"


class DevRunner:
    """pw-dump answers from a fixture; restarts and pw-cat are recorded, never run."""

    def __init__(self, dump=None, missing=False, restart_rc=0):
        self.calls = []
        self.dump = dump if dump is not None else json.loads((FIX / "pwdump_gcs8.json").read_text())
        self.missing = missing
        self.restart_rc = restart_rc

    def __call__(self, argv):
        if self.missing:
            raise FileNotFoundError(argv[0])
        self.calls.append(argv)
        if argv == pd.DUMP_CMD:
            return 0, json.dumps(self.dump)
        if argv == pd.RESTART_CMD:
            return self.restart_rc, "" if self.restart_rc == 0 else "unit failed"
        return 0, ""

    @property
    def restarts(self):
        return [c for c in self.calls if c == pd.RESTART_CMD]


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("PWCTL_DEVICES", raising=False)
    monkeypatch.setattr(pwctl, "SETTLE", lambda: None)
    return tmp_path


def run(*argv, runner=None, answer="n"):
    runner = runner or DevRunner()
    out, err = io.StringIO(), io.StringIO()
    asked = []

    def ask(q):
        asked.append(q)
        return answer

    rc = pwctl.main(["devices", *argv], runner=runner, out=out, err=err, ask=ask)
    return rc, out.getvalue(), err.getvalue(), runner, asked


def applied(m=None):
    """The fixture dump plus every node an apply of m should create."""
    m = m or pd.default_model()
    d = json.loads((FIX / "pwdump_gcs8.json").read_text())
    for p in m["pairs"]:
        if p["enabled"]:
            name = pd.pair_node_name(p)
            d.append({"type": "PipeWire:Interface:Node", "info": {"props": {"node.name": name}}})
            d.append({"type": "PipeWire:Interface:Node", "info": {"props": {
                "node.name": f"{name}.hw", "audio.position": f"[ AUX{p['aux']} AUX{p['aux'] + 1} ]"}}})
    d += [{"type": "PipeWire:Interface:Node", "info": {"props": {"node.name": b["name"]}}} for b in m["buses"]]
    return d


# --- list --------------------------------------------------------------------

def test_list_shows_all_pairs_grouped_with_status():
    rc, out, _, r, _ = run("list", runner=DevRunner(dump=applied()))
    assert rc == 0
    assert out.index("Inputs") < out.index("GCS-8 CH 2 (Mic)") < out.index("Outputs") < out.index("GCS-8 CH 3-AUX (Music)")
    assert out.count(" ok ") == 17 + 2
    assert "AUX8/9" in out
    assert "SYSTEM" in out and "GAME" in out
    assert "defaults" in out  # no devices.json yet


def test_list_is_default_subcommand():
    assert run()[1] == run("list")[1]


def test_list_before_apply_marks_pairs_missing():
    rc, out, *_ = run("list")
    assert rc == 0 and out.count("missing") == 17 + 2


def test_list_shows_disabled_pairs_as_off(xdg):
    m = pd.default_model()
    m["pairs"][0]["enabled"] = False
    pd.save_model(pd.devices_path(), m)
    _, out, *_ = run("list", runner=DevRunner(dump=applied(m)))
    assert out.count(" off ") == 1 and "defaults" not in out


def test_list_without_pipewire_still_lists_model():
    rc, out, err, *_ = run("list", runner=DevRunner(missing=True))
    assert rc == 0 and "GCS-8 CH 2 (Mic)" in out and " ? " in out and "pw-dump" in err


# --- preview -----------------------------------------------------------------

def test_preview_shows_diff_and_writes_nothing(xdg):
    rc, out, _, r, _ = run("preview")
    assert rc == 0 and out.startswith("--- ")
    assert not pd.dropin_path().exists() and not pd.devices_path().exists()
    assert r.restarts == []


def test_preview_when_current_says_so(xdg):
    pd.write_dropin(pd.dropin_path(), pd.render_dropin(pd.default_model()))
    assert "up to date" in run("preview")[1]


# --- apply -------------------------------------------------------------------

def test_apply_declined_writes_nothing(xdg):
    rc, out, _, r, asked = run("apply", answer="n")
    assert rc == 1 and "nothing written" in out
    assert asked and "restart" in asked[0].lower()
    assert not pd.dropin_path().exists()
    assert r.restarts == []


def test_apply_confirmed_writes_saves_restarts_and_checks(xdg):
    rc, out, _, r, _ = run("apply", answer="y", runner=DevRunner(dump=applied()))
    assert rc == 0
    assert pd.dropin_path().read_text() == pd.render_dropin(pd.default_model())
    assert pd.load_model(pd.devices_path()) == pd.default_model()
    assert r.restarts == [pd.RESTART_CMD]
    assert r.calls.index(pd.RESTART_CMD) < len(r.calls) - 1 and r.calls[-1] == pd.DUMP_CMD
    assert "all 17 pairs and 2 buses present" in out


def test_apply_reports_missing_nodes_after_restart(xdg):
    rc, out, err, *_ = run("apply", answer="y")  # fixture dump has no pwctl nodes
    assert rc == 1 and "missing" in err and "rollback" in err


def test_apply_with_nothing_to_change_skips_restart(xdg):
    pd.write_dropin(pd.dropin_path(), pd.render_dropin(pd.default_model()))
    rc, out, _, r, asked = run("apply", answer="y")
    assert rc == 0 and "up to date" in out and asked == [] and r.restarts == []


def test_apply_refuses_foreign_dropin(xdg):
    path = pd.dropin_path()
    path.parent.mkdir(parents=True)
    path.write_text("context.modules = [ ]\n")
    rc, _, err, r, _ = run("apply", answer="y")
    assert rc == 1 and "not generated by pwctl" in err and r.restarts == []


def test_apply_restart_failure_reports(xdg):
    rc, _, err, *_ = run("apply", answer="y", runner=DevRunner(restart_rc=1))
    assert rc == 1 and "unit failed" in err


def test_apply_invalid_devices_json_exit_2(xdg):
    path = pd.devices_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**pd.default_model(), "clock_rate": 1}))
    rc, _, err, r, _ = run("apply", answer="y")
    assert rc == 2 and "clock" in err and r.restarts == []


# --- rollback ----------------------------------------------------------------

def test_rollback_confirmed_restores_and_restarts(xdg):
    pd.write_dropin(pd.dropin_path(), pd.render_dropin(pd.default_model()))
    rc, out, _, r, _ = run("rollback", answer="y")
    assert rc == 0 and "removed" in out and not pd.dropin_path().exists()
    assert r.restarts == [pd.RESTART_CMD]


def test_rollback_declined_keeps_file(xdg):
    pd.write_dropin(pd.dropin_path(), pd.render_dropin(pd.default_model()))
    rc, *_ , r, _ = run("rollback", answer="n")
    assert rc == 1 and pd.dropin_path().exists() and r.restarts == []


def test_rollback_with_nothing_skips_restart(xdg):
    rc, out, _, r, asked = run("rollback", answer="y")
    assert rc == 0 and "nothing" in out and asked == [] and r.restarts == []


# --- check -------------------------------------------------------------------

def test_check_ok_and_missing():
    assert run("check", runner=DevRunner(dump=applied()))[0] == 0
    rc, _, err, *_ = run("check")
    assert rc == 1 and "pwctl.gcs8.in.ch2: missing" in err


# --- test PAIR ---------------------------------------------------------------

def test_test_plays_noise_through_output_pair(xdg):
    rc, out, _, r, _ = run("test", "ch3aux", runner=DevRunner(dump=applied()))
    assert rc == 0
    play = [c for c in r.calls if c[0] == "pw-cat"]
    assert len(play) == 1
    assert play[0][:4] == ["pw-cat", "--playback", "--target", "pwctl.gcs8.out.ch3aux"]
    assert "GCS-8 CH 3-AUX (Music)" in out and "AUX8/9" in out


@pytest.mark.parametrize("pair", ["ch2", "nope"])
def test_test_rejects_unknown_or_input_pair(pair):
    rc, _, err, r, _ = run("test", pair)
    assert rc == 2 and not [c for c in r.calls if c[0] == "pw-cat"]


def test_test_missing_node_says_apply_first():
    rc, _, err, *_ = run("test", "ch3aux")
    assert rc == 1 and "apply" in err


def test_noise_wav_is_short_quiet_stereo(tmp_path):
    path = tmp_path / "n.wav"
    pwctl.write_noise(path)
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 2 and w.getframerate() == 48000 and w.getsampwidth() == 2
        assert 0.5 <= w.getnframes() / 48000 <= 2
        frames = w.readframes(w.getnframes())
    peak = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True)) for i in range(0, len(frames), 2))
    assert 0 < peak <= 8192


def test_bad_subcommand_exit_2():
    assert run("frobnicate")[0] == 2
