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


def test_patch_has_no_view_line_and_up_from_the_first_source_stays_put():
    # Skrappjaw, 2026-10-26: "View menu can go away. not needed. patch bay shortcuts on screen are enough."
    st, _ = key(patch(), "up", patch=G)
    assert st.patch_src == 0 and not hasattr(st, "patch_head")
    text = "\n".join(t for t, _ in pc.render_screen(st, CUR, "", 100, 40, patch=G)[0])
    assert "View:" not in text


def test_patch_keys_grid_monitors_and_ports_are_on_screen():
    st = pc.UIState(tab="patch")
    keys = [k for k, _ in pc.legend(st)]
    assert {"g", "m", "p"} <= set(keys)
    grid, _ = key(st, "g", patch=G)
    assert grid.patch_view == "grid"
    assert key(st, "m", patch=G)[0].patch_monitors


# --- Settings -----------------------------------------------------------------------------------------

def test_restart_is_a_clock_button():
    st = pc.UIState(tab="settings", settings_cat="audio", settings_focus="pane", cursor=pc.audio_index(("buttons",)),
                    key_box=False)
    assert pc.CLOCK_BUTTONS[-1] == "Restart"
    st, _ = key(st, *["right"] * len(pc.CLOCK_BUTTONS), "enter")
    assert st.popup == ("restart",)


# --- touch: tap the row (Skrappjaw, 2026-10-26) ----------------------------------------------------------------

def tap(st, target, **kw):
    lines, hits = pc.render_screen(st, CUR, "", 100, 40, devices=DV, patch=G, **kw)
    row, c0, _ = next((r, a, b) for r, a, b, t in hits if t == target)
    return pc.handle_mouse(st, hits, row, c0, "click", CUR, devices=DV, patch=G)


def test_a_tap_selects_and_a_second_tap_is_enter():
    card = dev_line("card")
    st = devices(dev_cursor=card + 1)                       # start somewhere else
    st, _ = tap(st, ("device", card))
    assert st.dev_cursor == card and not st.dev_menu        # first tap: select
    st, _ = tap(st, ("device", card))
    assert st.dev_menu                                      # second tap: Enter opens its menu


def test_a_tap_on_a_selected_patch_destination_connects():
    st, _ = key(patch(), "right", patch=G)                  # into the destinations
    _, a1 = tap(st, ("patch_dst", st.patch_dst))
    assert a1 == key(st, "enter", patch=G)[1] and a1
