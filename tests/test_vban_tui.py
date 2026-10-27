"""Tui._do_vban against a fake runner: the order of redraws and prompts, and what gets saved."""
import json

import pytest

import pipewrench
import pwcore as pc
import pwvban as pv
from test_vban_cli import Runner


@pytest.fixture(autouse=True)
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(pipewrench.socket, "gethostname", lambda: "desktop")
    return tmp_path


def tui(answers):
    t = pipewrench.Tui.__new__(pipewrench.Tui)
    t.runner, t.st, t.vban_version, t.vban_linked, t.calls = Runner(), pc.UIState(tab="settings", settings_cat="network"), None, 0.0, []
    t._draw = lambda scr: t.calls.append(("draw", t.st.popup))
    t._prompt = lambda scr, q, default="": (t.calls.append(("prompt", q)), answers.pop(0))[1]
    return t


def test_manual_redraws_without_the_popover_before_asking():
    """Skrappjaw, 2026-10-26: after Manual the Add popover stayed on screen under the prompt."""
    t = tui(["Studio", "192.168.1.86"])
    st, actions = pc.handle_key(pc.UIState(tab="settings", settings_cat="network", settings_focus="pane",
                                           popup=("vban_add", "in"), popup_button=1), "enter", {})
    assert actions == [("vban_manual", "in")] and st.popup is None
    t.st = st
    t._do_vban(None, actions[0])
    assert t.calls[0] == ("draw", None)  # drawn with no popover, then the questions
    assert [c[1] for c in t.calls if c[0] == "prompt"] == ["device name (empty cancels):", "Studio's IP address (empty cancels):"]
    e = json.loads(pv.config_path().read_text())["incoming"][0]
    assert (e["name"], e["ip"], e["stream"]) == ("Studio", "192.168.1.86", "")
    assert t.st.pick[3] == "Select Playback Destination"  # then the quick pick


def test_manual_empty_name_cancels():
    t = tui([""])
    t._do_vban(None, ("vban_manual", "in"))
    assert t.st.message == "cancelled" and not pv.config_path().exists()


def test_manual_bad_ip_says_why():
    t = tui(["Studio", "studio.local"])
    with pytest.raises(ValueError, match="IPv4"):  # _do turns this into the red band
        t._do_vban(None, ("vban_manual", "in"))


def test_rename():
    t = tui(["Studio", "192.168.1.86", "Studio desk"])
    t._do_vban(None, ("vban_manual", "in"))
    t._do_vban(None, ("vban_rename", "in", "studio"))
    assert json.loads(pv.config_path().read_text())["incoming"][0]["name"] == "Studio desk"
    assert t.st.message == "renamed Studio to Studio desk"


def test_remove_then_add_back():
    """The other half of Skrappjaw's report: delete the Studio, add it again."""
    t = tui(["Studio", "192.168.1.86", "Studio", "192.168.1.86"])
    t._do_vban(None, ("vban_manual", "in"))
    t._do_vban(None, ("vban_remove", "in", "studio", "Studio"))
    assert json.loads(pv.config_path().read_text())["incoming"] == []
    t._do_vban(None, ("vban_manual", "in"))
    e = json.loads(pv.config_path().read_text())["incoming"]
    assert [x["id"] for x in e] == ["studio"] and e[0]["port"] == 6980
    assert (pv.conf_dir() / "in-studio.conf").exists()


def test_outgoing_detect_lists_machines_then_asks_for_the_source(monkeypatch):
    f = pv.Found("192.168.1.86", "studio-pc", (22,), True)
    seen = {}
    monkeypatch.setattr(pipewrench, "_find_machines", lambda runner, address, known: (seen.update(known=known), [f])[1])
    t = tui(["Studio", "192.168.1.86"])
    t._do_vban(None, ("vban_manual", "in"))  # an incoming stream: a known VBAN sender
    t._do_vban(None, ("vban_detect", "out"))
    assert seen["known"] == {"192.168.1.86": "Studio"}
    assert t.st.pick[0] == "found" and t.calls[-1] == ("draw", None)  # "searching the network…" shown first
    t._do_vban(None, ("vban_found", "out", f))
    e = json.loads(pv.config_path().read_text())["outgoing"][0]
    assert (e["name"], e["ip"], e["stream"]) == ("studio-pc", "192.168.1.86", "desktop")
    assert t.st.pick[3] == "Select Source Audio"


def test_outgoing_detect_finding_nothing(monkeypatch):
    monkeypatch.setattr(pipewrench, "_find_machines", lambda runner, address, known: [])
    t = tui([])
    t._do_vban(None, ("vban_detect", "out"))
    assert t.st.message == "found no computers on this network; use Manual" and t.st.pick is None
