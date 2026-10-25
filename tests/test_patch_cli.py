import io
import json
from pathlib import Path

import pytest

import pipewrench

FIX = Path(__file__).parent / "fixtures"


class Runner:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, argv):
        self.calls.append(argv)
        if argv == ["pw-dump"]:
            return 0, (FIX / "pwdump_patch.json").read_text()
        if self.fail and argv[0] == "pw-link":
            return 1, "failed to link ports: Operation not permitted"
        return 0, ""

    def links(self):
        return [c for c in self.calls if c[0] == "pw-link"]


def run(*argv, runner=None):
    runner = runner or Runner()
    out, err = io.StringIO(), io.StringIO()
    rc = pipewrench.main(["patch", *argv], runner=runner, out=out, err=err)
    return rc, out.getvalue(), err.getvalue(), runner


def test_list_shows_each_connection_with_channels():
    rc, out, _, r = run("list")
    assert rc == 0 and r.links() == []
    assert "Discord → GCS-8 CH 4-AUX" in out and "GCS-8 CH 2 → Discord" in out
    assert "FL→FL, FR→FR" in out


def test_list_is_default():
    assert run()[1] == run("list")[1]


def test_connect_links_both_channels():
    rc, out, err, r = run("connect", "GCS-8 CH 1", "GCS-8 CH 3-AUX")
    assert rc == 0, err
    assert len(r.links()) == 2 and all(c[1] != "-d" for c in r.links())
    assert "connected" in out


def test_connect_existing_is_a_no_op():
    rc, out, _, r = run("connect", "Discord", "GCS-8 CH 4-AUX")
    assert rc == 0 and r.links() == [] and "already" in out


def test_disconnect_removes_links():
    rc, out, _, r = run("disconnect", "Discord", "GCS-8 CH 4-AUX")
    assert rc == 0 and len(r.links()) == 2 and all(c[:2] == ["pw-link", "-d"] for c in r.links())
    rc, out, _, r = run("disconnect", "GCS-8 CH 1", "GCS-8 CH 3-AUX")
    assert rc == 0 and r.links() == [] and "not connected" in out


def test_monitor_source_by_label():
    rc, _, err, r = run("connect", "GCS-8 CH USB (monitor)", "Discord")
    assert rc == 0, err and len(r.links()) == 2


@pytest.mark.parametrize("args", [("connect", "nope", "GCS-8 CH 3-AUX"), ("connect", "GCS-8 CH 1", "nope"),
                                  ("connect", "GCS-8 CH 1"), ("frobnicate",)])
def test_bad_input_exit_2(args):
    rc, _, err, r = run(*args)
    assert rc == 2 and err and r.links() == []


def test_pw_link_failure_exit_1():
    rc, _, err, _ = run("connect", "GCS-8 CH 1", "GCS-8 CH 3-AUX", runner=Runner(fail=True))
    assert rc == 1 and "Operation not permitted" in err
