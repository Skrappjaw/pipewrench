import io
from pathlib import Path

import pytest

import pwcore as pc
import pipewrench

FIX = Path(__file__).parent / "fixtures"
READ = ["pw-metadata", "-n", "settings", "0"]


class FakeRunner:
    def __init__(self, missing=False, fail_writes=False):
        self.calls = []
        self.missing = missing
        self.fail_writes = fail_writes

    def __call__(self, argv):
        if self.missing:
            raise FileNotFoundError(argv[0])
        self.calls.append(argv)
        if argv == READ:
            return 0, (FIX / "settings.txt").read_text()
        return (1, "write failed") if self.fail_writes else (0, "")

    @property
    def writes(self):
        return [c for c in self.calls if c != READ]


@pytest.fixture(autouse=True)
def presets_file(tmp_path, monkeypatch):
    path = tmp_path / "presets.json"
    monkeypatch.setenv("PIPEWRENCH_PRESETS", str(path))
    return path


def run(*argv, runner=None, execvp=None):
    runner = runner or FakeRunner()
    out, err = io.StringIO(), io.StringIO()
    rc = pipewrench.main(list(argv), runner=runner, out=out, err=err, execvp=execvp)
    return rc, out.getvalue(), err.getvalue(), runner


def test_status():
    rc, out, _, r = run("status")
    assert rc == 0
    assert "force rate" in out and "48000" in out
    assert "force buffer" in out and "128" in out
    assert "allowed rates" in out
    assert r.writes == []


@pytest.mark.parametrize("cmd,key", [("rate", "clock.force-rate"), ("buf", "clock.force-quantum"),
                                     ("min", "clock.min-quantum"), ("max", "clock.max-quantum"),
                                     ("log", "log.level")])
def test_setters(cmd, key):
    value = {"rate": "48000", "buf": "256", "min": "64", "max": "1024", "log": "3"}[cmd]
    rc, _, _, r = run(cmd, value)
    assert rc == 0
    assert r.writes == [pc.set_cmd(key, int(value))]


def test_setter_warns_but_applies():
    rc, _, err, r = run("rate", "96000")
    assert rc == 0 and "allowed" in err
    assert r.writes == [pc.set_cmd("clock.force-rate", 96000)]


@pytest.mark.parametrize("argv", [("buf", "lots"), ("buf",), ("min", "4096"), ("rate", "-5")])
def test_bad_values_exit_2(argv):
    rc, _, err, r = run(*argv)
    assert rc == 2 and err
    assert r.writes == []


def test_unknown_command_exit_2():
    rc, _, err, _ = run("frobnicate")
    assert rc == 2 and "usage" in err.lower()


def test_reset():
    rc, _, _, r = run("reset")
    assert rc == 0
    assert r.writes == [pc.set_cmd("clock.force-rate", 0), pc.set_cmd("clock.force-quantum", 0)]


def test_missing_pw_metadata_is_clear_error():
    rc, _, err, _ = run("status", runner=FakeRunner(missing=True))
    assert rc == 1 and "pw-metadata" in err


def test_failed_write_reports():
    rc, _, err, _ = run("buf", "256", runner=FakeRunner(fail_writes=True))
    assert rc == 1 and "write failed" in err


def test_top_execs_pw_top():
    seen = []
    rc, *_ = run("top", execvp=lambda f, a: seen.append((f, a)))
    assert seen == [("pw-top", ["pw-top"])]


def test_preset_save_list_apply_delete(presets_file):
    rc, _, _, r = run("preset", "save", "now", "Current setup")
    assert rc == 0 and r.writes == []
    assert pc.find_preset(pc.load_presets(presets_file), "now")["label"] == "Current setup"

    rc, out, _, _ = run("preset", "list")
    assert "now" in out and "Current setup" in out

    rc, _, _, r = run("preset", "apply", "now")
    assert rc == 0
    assert r.writes == pc.preset_cmds(pc.find_preset(pc.load_presets(presets_file), "now"),
                                      pc.parse_settings((FIX / "settings.txt").read_text()))

    rc, *_ = run("preset", "delete", "now")
    assert rc == 0 and pc.load_presets(presets_file) == []


def test_preset_errors():
    assert run("preset", "apply", "ghost")[0] == 2
    assert run("preset", "delete", "ghost")[0] == 2
    assert run("preset", "save", "bad name", "x")[0] == 2
    run("preset", "save", "a", "A")
    assert run("preset", "save", "a", "again")[0] == 2


def test_preset_list_empty():
    rc, out, _, _ = run("preset", "list")
    assert rc == 0 and "no presets" in out
