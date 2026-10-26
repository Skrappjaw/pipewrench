"""Every letter shortcut's job has an arrow + Enter path, so shortcut keys can be off (Skrappjaw, 2026-10-26: "good
navigation doesnt need them"). These tests use only ↑↓←→, Enter and Esc, with the Keys box hidden."""
import json
from pathlib import Path

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi
import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
INV = pi.inventory(json.loads((FIX / "pwdump_inventory.json").read_text()))
G = pp.graph(json.loads((FIX / "pwdump_patch.json").read_text()))
DV = pc.devices_view(INV, pcfg.default_config())


def key(st, *keys, **kw):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, **kw)
        actions += a
    return st, actions


def dev_line(kind, label=None):
    items = pc.dev_selectable(DV, pc.UIState(tab="devices"))
    return next(i for i, (k, o) in enumerate(items) if k == kind and (label is None or o.label == label))


def dev_menu(st):
    return [label for label, _ in pc.dev_menu_items(DV, st)]


def devices(**kw):
    return pc.UIState(tab="devices", key_box=False, **kw)


def pick(st, label, menu=dev_menu, **kw):
    """Arrow down to a menu item by its label and press Enter."""
    items = menu(st)
    n = next(i for i, x in enumerate(items) if x.startswith(label))
    return key(st, *["down"] * n, "enter", **kw)


# --- Devices --------------------------------------------------------------------------------------

def test_a_bus_menu_offers_test_noise():
    st, _ = key(devices(dev_cursor=dev_line("row", "VOIP Audio/Duplex sink")), "enter", devices=DV)
    assert "Test noise" in dev_menu(st)
    assert pick(st, "Test noise", devices=DV)[1] == [("dev_test", INV.find("VOIP").name)]


# --- Patch ------------------------------------------------------------------------------------------

def patch(**kw):
    return pc.UIState(tab="patch", key_box=False, **kw)


def head_menu(st):
    return [label for label, _ in pc.patch_head_items(st)]


def test_up_from_the_first_source_reaches_the_view_line():
    st, _ = key(patch(), "up", patch=G)
    assert st.patch_head and not st.patch_head_menu
    text = "\n".join(t for t, _ in pc.render_screen(st, CUR, "", 100, 40, patch=G)[0])
    assert "View:" in text
    assert not key(st, "down", patch=G)[0].patch_head


def test_patch_view_menu_switches_grid_monitors_and_ports():
    st, _ = key(patch(), "up", "enter", patch=G)
    assert st.patch_head_menu and head_menu(st) == ["Grid view", "Monitors: off", "Port by port", "Back"]
    grid, _ = pick(st, "Grid view", menu=head_menu, patch=G)
    assert grid.patch_view == "grid" and not grid.patch_head_menu
    mon, _ = pick(st, "Monitors", menu=head_menu, patch=G)
    assert mon.patch_monitors
    ports, _ = pick(st, "Port by port", menu=head_menu, patch=G)
    assert ports.patch_ports and not ports.patch_head
    assert key(st, "esc", patch=G)[0].patch_head_menu is False


# --- Settings -----------------------------------------------------------------------------------------

def test_restart_is_a_clock_button():
    st = pc.UIState(tab="settings", settings_cat="audio", settings_focus="pane", cursor=pc.audio_index(("buttons",)),
                    key_box=False)
    assert pc.CLOCK_BUTTONS[-1] == "Restart"
    st, _ = key(st, *["right"] * len(pc.CLOCK_BUTTONS), "enter")
    assert st.popup == ("restart",)
