"""Shortcut keys on or off (Skrappjaw, 2026-10-26: "good navigation doesnt need them", as a toggle "so i can AB test").
Off: letter shortcuts do nothing and leave the Keys box; arrows, Enter, Esc, Space, Tab, 1–6, media keys and q work."""
import json
from pathlib import Path

import pytest

import pwcore as pc
import pwkeys as pk

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())


def prefs(**kw):
    return pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane", **kw)


def key(st, *keys):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR)
        actions += a
    return st, actions


def body(st):
    return "\n".join(t for t, _ in pc.render_screen(st, CUR, "", 100, 70)[0])


def test_display_has_meters_and_shortcut_keys_rows():
    text = body(prefs())
    assert "Meters" in text and "Shortcut keys" in text
    assert pc.METERS_ROW == pc.BAR_ROW + 1 and pc.SHORTCUTS_ROW == pc.METERS_ROW + 1
    assert pc.KEYS_ROW == pc.SHORTCUTS_ROW + 1


def test_the_rows_toggle_and_save():
    st, actions = key(prefs(cursor=pc.SHORTCUTS_ROW), "enter", "right")
    assert st.shortcuts is False and actions == [("save_ui",)]
    st, actions = key(prefs(cursor=pc.METERS_ROW), "enter", "right")
    assert st.meters is False and actions == [("save_ui",)]
    ui = pc.ui_prefs(pc.UIState(shortcuts=False, meters=False))
    assert ui["shortcuts"] is False and ui["meters"] is False


def test_ui_json_keeps_them(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"shortcuts": False, "meters": False}))
    assert pc.load_ui(p)["shortcuts"] is False and pc.load_ui(p)["meters"] is False
    p.write_text(json.dumps({"shortcuts": "no"}))
    assert pc.load_ui(p)["shortcuts"] is True                      # bad values give the default: on


@pytest.mark.parametrize("a", pk.ACTIONS, ids=lambda a: a.id)
def test_off_swallows_every_shortcut(a):
    st = pc.UIState(tab=a.screen, shortcuts=False)
    after, actions = pc.handle_key(st, a.default, CUR)
    assert actions == [] and after.tab == a.screen


def test_off_leaves_navigation_alone():
    st = pc.UIState(tab="mixer", shortcuts=False)
    assert key(st, "2")[0].tab == "devices" and key(st, "tab")[0].tab == "devices"
    assert key(st, "*")[1] == [("media", "play/pause")]
    assert key(st, "q")[0].popup == ("quit",)


def test_off_drops_the_chips():
    on = [k for k, _ in pc.legend(pc.UIState(tab="devices"))]
    off = [k for k, _ in pc.legend(pc.UIState(tab="devices", shortcuts=False))]
    defaults = {a.default for a in pk.ACTIONS if a.screen == "devices"}
    assert any(k.split()[0] in defaults for k in on)
    assert not any(k.split()[0] in defaults for k in off) and "Enter" in off


def test_the_keys_page_says_they_are_off():
    text = body(prefs(shortcuts=False))
    assert "shortcut keys are off" in text


def test_every_letter_key_is_a_listed_action():
    assert pk.BY_ID["devices.preview"].default == "v"
    assert "y" not in pk.GLOBAL and "n" not in pk.GLOBAL
