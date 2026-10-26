"""Devices without View…/Actions…/Preview/Apply, and state that survives a restart (Skrappjaw, 2026-10-26: "that diff
is not a user friendly thing. eliminate the view. What we need is for this to always save the on/off/hidden
state so that it survives resets")."""
import json
from pathlib import Path

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi
import pwkeys as pk

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
INV = pi.inventory(json.loads((FIX / "pwdump_inventory.json").read_text()))


def dv(**kw):
    return pc.devices_view(INV, pcfg.default_config(), **kw)


def key(st, *keys, **kw):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, **kw)
        actions += a
    return st, actions


def text(st, devices=None, height=60):
    return "\n".join(t for t, _ in pc.render_screen(st, CUR, "", 100, height, devices=devices)[0])


# --- View… and Actions… are gone ------------------------------------------------------------------

def test_no_view_or_actions_lines():
    kinds = [k for k, _ in pc._dev_lines(dv(), pc.UIState(tab="devices"))]
    assert "view" not in kinds and "actions" not in kinds
    out = text(pc.UIState(tab="devices"), dv())
    assert "View…" not in out and "Actions…" not in out


def test_no_preview_or_apply_anywhere():
    ids = {a.id for a in pk.ACTIONS}
    assert "devices.preview" not in ids and "devices.apply" not in ids
    st = pc.UIState(tab="devices")
    for k in ("v", "A"):
        assert key(st, k, devices=dv())[1] == []
    labels = [label for label, _ in pc.legend(st)]
    assert not any("apply" in label or "preview" in label for label in labels)
    for i in range(len(pc.dev_selectable(dv(), st))):
        menu = pc.dev_menu_items(dv(), pc.UIState(tab="devices", dev_cursor=i, dev_menu=("x", None)))
        assert not any(label in ("Preview files", "Apply files") for label, _ in menu)


def test_old_bindings_for_removed_actions_are_dropped():
    assert pk.clean({"devices.apply": "z", "devices.preview": "y", "devices.monitors": "M"}) == {"devices.monitors": "M"}


def test_no_note_about_writing_files():
    out = text(pc.UIState(tab="devices"), dv())
    assert "A writes" not in out and "generated files" not in out


def test_pending_bus_says_how_to_create_it():
    line, _ = pc._device_line_text("pending", {"name": "fx", "label": "FX"}, dv(), False)
    assert "A creates it" not in line and "Create now" in line


# --- Show hidden and Monitors are saved ------------------------------------------------------------

def test_h_and_m_toggle_and_save():
    st = pc.UIState(tab="devices")
    st, actions = key(st, "h", devices=dv())
    assert st.dev_show_hidden is True and actions == [("save_ui",)]
    st, actions = key(st, "m", devices=dv())
    assert st.dev_monitors is True and actions == [("save_ui",)]
    ui = pc.ui_prefs(st)
    assert ui["dev_show_hidden"] is True and ui["dev_monitors"] is True


def test_ui_json_keeps_them(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"dev_show_hidden": True, "dev_monitors": True}))
    assert pc.load_ui(p)["dev_show_hidden"] is True and pc.load_ui(p)["dev_monitors"] is True
    p.write_text(json.dumps({"dev_show_hidden": "yes"}))
    assert pc.load_ui(p)["dev_show_hidden"] is False and pc.load_ui(p)["dev_monitors"] is False   # default off


def test_preferences_rows_toggle_them():
    prefs = pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane")
    assert pc.SHOW_HIDDEN_ROW == pc.METERS_ROW + 1 and pc.MONITORS_ROW == pc.SHOW_HIDDEN_ROW + 1
    assert pc.KEY_BOX_ROW == pc.MONITORS_ROW + 1
    out = text(prefs, height=70)
    assert "Show hidden devices" in out and "Monitors on Devices" in out
    st, actions = key(pc.dataclasses.replace(prefs, cursor=pc.SHOW_HIDDEN_ROW), "enter", "right")
    assert st.dev_show_hidden is True and actions == [("save_ui",)]
    st, actions = key(pc.dataclasses.replace(prefs, cursor=pc.MONITORS_ROW), "enter", "right")
    assert st.dev_monitors is True and actions == [("save_ui",)]


# --- Reset never touches it ------------------------------------------------------------------------

def test_settings_reset_only_loads_clock_values():
    st = pc.UIState(tab="settings", settings_cat="audio", settings_focus="pane", cursor=pc.audio_index(("buttons",)),
                    dev_show_hidden=True, dev_monitors=True)
    st, actions = key(st, *["right"] * pc.CLOCK_BUTTONS.index("Reset"), "enter")
    assert actions == [] and set(st.pending) <= set(pc.CLOCK_DEFAULTS)
    assert st.dev_show_hidden is True and st.dev_monitors is True
