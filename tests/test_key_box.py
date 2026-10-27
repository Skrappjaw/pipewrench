"""Display → Keys box hides the key list at the bottom of the screen (Skrappjaw, 2026-10-26: "im meant to hide the
shortcut bar on screen. not disable shortcuts entirely"). Off: no box, no chips, the body gets those lines;
every key, letter shortcuts included, still works."""
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


def screen(st, width=100, height=40):
    return pc.render_screen(st, CUR, "", width, height)


def body(st):
    return "\n".join(t for t, _ in screen(st, height=70)[0])


def test_display_has_meters_and_keys_box_rows():
    text = body(prefs())
    assert "Meters" in text and "Keys box" in text and "Shortcut keys" not in text
    assert "the key list at the bottom of the screen" in body(prefs(cursor=pc.KEY_BOX_ROW))   # on the status line
    assert pc.METERS_ROW == pc.BAR_ROW + 1 and pc.KEY_BOX_ROW == pc.MONITORS_ROW + 1
    assert pc.KEYS_ROW == pc.KEY_BOX_ROW + 1


def test_the_rows_toggle_and_save():
    st, actions = key(prefs(cursor=pc.KEY_BOX_ROW), "enter", "right")
    assert st.key_box is False and actions == [("save_ui",)]
    st, actions = key(prefs(cursor=pc.METERS_ROW), "enter", "right")
    assert st.meters is False and actions == [("save_ui",)]
    ui = pc.ui_prefs(pc.UIState(key_box=False, meters=False))
    assert ui["key_box"] is False and ui["meters"] is False and "shortcuts" not in ui


def test_ui_json_keeps_it(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"key_box": False, "meters": False}))
    assert pc.load_ui(p)["key_box"] is False and pc.load_ui(p)["meters"] is False
    p.write_text(json.dumps({"key_box": "no"}))
    assert pc.load_ui(p)["key_box"] is True                        # bad values give the default: on
    p.write_text(json.dumps({"shortcuts": False}))                 # f8b2892's key meant something else: dropped
    assert pc.load_ui(p)["key_box"] is True and "shortcuts" not in pc.load_ui(p)


@pytest.mark.parametrize("a", pk.ACTIONS, ids=lambda a: a.id)
def test_every_shortcut_works_with_the_box_hidden(a):
    on = pc.handle_key(pc.UIState(tab=a.screen), a.default, CUR)
    off = pc.handle_key(pc.UIState(tab=a.screen, key_box=False), a.default, CUR)
    assert off[1] == on[1] and pc.dataclasses.replace(off[0], key_box=True) == on[0]


def test_hidden_box_leaves_no_footer_and_no_chips():
    on, on_hits = screen(pc.UIState(tab="mixer"))
    off, off_hits = screen(pc.UIState(tab="mixer", key_box=False))
    assert any(t.startswith("┌─ Keys") for t, _ in on)
    assert not any(t.startswith("┌─ Keys") for t, _ in off)
    media = {"*", "-", "+", "<", ">"}                              # the now-playing bar keeps its chips
    assert {h[3][1] for h in on_hits if h[3][0] == "key"} - media
    assert {h[3][1] for h in off_hits if h[3][0] == "key"} <= media
    assert len(off) == len(on) == 40                               # same height: the body takes the box's lines


def test_the_body_gets_the_lines():
    st = prefs()                                                   # the Keys list runs past 30 lines
    shown = lambda st: sum(1 for t, _ in screen(st, height=30)[0] if t[pc.MENU_W - 1:pc.MENU_W] == "│")  # pane lines
    footer = sum(1 for t, _ in screen(st, height=30)[0] if t[:1] in "┌│└")
    assert footer >= 3 and shown(pc.dataclasses.replace(st, key_box=False)) > shown(st)


def test_the_keys_page_is_not_greyed():
    text = body(prefs(key_box=False))
    assert "are off" not in text
    rows = screen(prefs(key_box=False), height=70)[0]
    first = next(s for t, s in rows if pc.binding_label(pk.ACTIONS[0]) in t and "◀" not in t)
    assert (first[0] if isinstance(first, tuple) else first) == "normal"


def test_every_letter_key_is_a_listed_action():
    assert "devices.preview" not in pk.BY_ID and "devices.apply" not in pk.BY_ID  # no Preview/Apply (2026-10-26)
    assert "y" not in pk.GLOBAL and "n" not in pk.GLOBAL
