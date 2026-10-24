import json
from pathlib import Path

import pytest

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
CH2_IN = "alsa_input.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_in_GCS8_0_6_7__source"
CH3_OUT = "alsa_output.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_out_GCS8_0_8_9__sink"
INV = pi.inventory(json.loads((FIX / "pwdump_inventory.json").read_text()))


def dv(config=None, **kw):
    return pc.devices_view(INV, config or pcfg.default_config(), **kw)


def key(st, *keys, devices=None):
    devices = dv() if devices is None else devices
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, [], devices=devices)
        actions += a
    return st, actions


def at(target, devices=None):
    """A Devices-tab state with the cursor on a row (node name), card (label) or pending bus (name)."""
    entries = pc.dev_selectable(devices or dv(), pc.UIState(tab="devices"))
    for i, (kind, obj) in enumerate(entries):
        if (kind == "row" and obj.name == target) or (kind == "card" and obj.label == target) \
                or (kind == "pending" and obj["name"] == target):
            return pc.UIState(tab="devices", dev_cursor=i)
    raise AssertionError(target)


def screen(st, devices=None, width=100, height=40):
    return pc.render_screen(st, CUR, None, [], width, height, devices=dv() if devices is None else devices)


def body(lines):
    return "\n".join(t for t, _ in lines)


def hit_for(hits, target):
    for row, c0, c1, t in hits:
        if t == target:
            return row, c0, c1
    raise AssertionError(f"no hit for {target}: {[h[3] for h in hits]}")


# --- render ----------------------------------------------------------------------

def test_render_cards_rows_and_virtual():
    text = body(screen(pc.UIState(tab="devices"))[0])
    assert text.index("GCS-8 · profile HiFi") < text.index("GCS-8 CH 2 ") < text.index("Virtual") < text.index("VOIP_MIC")
    mic = next(line for line in text.splitlines() if "GCS-8 CH 2 " in line)
    assert "★" in mic and "running" in mic and "100%" in mic and " in " in mic
    assert "40%" in next(line for line in text.splitlines() if "GCS-8 CH 3-AUX" in line)


def test_render_cursor_starts_on_first_card_and_moves():
    lines, _ = screen(pc.UIState(tab="devices"))
    sel = [t for t, s in lines if s == "selected"]
    assert len(sel) == 1 and "Built-in Audio" in sel[0]
    st, _ = key(pc.UIState(tab="devices"), "down")
    sel = [t for t, s in screen(st)[0] if s == "selected"]
    assert "DualShock" in sel[0]


def test_hidden_rows_vanish_until_H():
    d = dv(pcfg.toggle_hidden(pcfg.default_config(), CH2_IN))
    assert "GCS-8 CH 2 " not in body(screen(pc.UIState(tab="devices"), d)[0])
    st, _ = key(pc.UIState(tab="devices"), "H", devices=d)
    assert st.dev_show_hidden
    lines, _ = screen(st, d)
    assert any("GCS-8 CH 2 " in t and s == "dim" for t, s in lines)


def test_m_shows_monitors():
    st, _ = key(pc.UIState(tab="devices"), "m")
    assert st.dev_monitors and "GCS-8 CH 3-AUX (monitor)" in body(screen(st)[0])


def test_pending_buses_and_dirty_note():
    c = pcfg.add_bus(pcfg.default_config(), "OBS")
    text = body(screen(pc.UIState(tab="devices"), dv(c, dirty=True, pending=c["buses"]), height=60)[0])
    assert "OBS" in text and "pending" in text and "not written" in text
    assert "not written" not in body(screen(pc.UIState(tab="devices"))[0])


def test_clash_tag_on_bus_made_outside_pwctl():
    text = body(screen(pc.UIState(tab="devices"), dv(clash={"SYSTEM"}))[0])
    assert "outside pwctl" in next(line for line in text.splitlines() if "SYSTEM" in line)


def test_scrolls_to_keep_cursor_visible():
    st = at("VOIP_MIC")
    lines, _ = screen(st, height=20)
    assert len(lines) == 20 and any(s == "selected" and "VOIP_MIC" in t for t, s in lines)


def test_loading_when_devices_missing():
    lines, _ = pc.render_screen(pc.UIState(tab="devices"), CUR, None, [], 80, 20, devices=None)
    assert "loading" in body(lines)


# --- keys: live actions --------------------------------------------------------------

def test_default_volume_mute_on_a_row():
    st = at(CH2_IN)
    assert key(st, "d")[1] == [("dev_cmd", ["wpctl", "set-default", "96"])]
    assert key(st, "right")[1] == [("dev_cmd", ["wpctl", "set-volume", "96", "5%+"])]
    assert key(st, "left")[1] == [("dev_cmd", ["wpctl", "set-volume", "96", "5%-"])]
    assert key(st, " ")[1] == [("dev_cmd", ["wpctl", "set-mute", "96", "toggle"])]


def test_row_actions_on_a_card_heading_explain():
    st, actions = key(at("GCS-8"), "d")
    assert actions == [] and st.message


def test_monitor_rows_refuse_commands():
    st = pc.UIState(tab="devices", dev_monitors=True)
    entries = pc.dev_selectable(dv(), st)
    i = next(i for i, (k, o) in enumerate(entries) if k == "row" and o.kind == "monitor")
    st, actions = key(pc.UIState(tab="devices", dev_monitors=True, dev_cursor=i), "d")
    assert actions == [] and "monitor" in st.message


def test_profile_from_card_or_its_row():
    gcs = INV.find_card("GCS-8")
    for target in ("GCS-8", CH2_IN):          # p opens the card's profile menu, same as Enter on its heading
        st, actions = key(at(target), "p")
        assert actions == [] and st.dev_menu == ("card", gcs.id)
    st, actions = key(at("SYSTEM"), "p")
    assert actions == [] and "card" in st.message


def test_test_noise_on_outputs_only():
    assert key(at(CH3_OUT), "t")[1] == [("dev_test", CH3_OUT)]
    st, actions = key(at(CH2_IN), "t")
    assert actions == [] and "output" in st.message


# --- keys: config edits ------------------------------------------------------------------

def test_hide_rows_and_cards():
    assert key(at(CH2_IN), "h")[1] == [("dev_hide", CH2_IN)]
    assert key(at("GCS-8"), "h")[1] == [("dev_hide", INV.find_card("GCS-8").name)]


def test_bus_add_and_live_delete_with_confirm():
    assert key(pc.UIState(tab="devices"), "a")[1] == [("dev_prompt_bus",)]
    st, actions = key(at("VOIP"), "x")  # any virtual bus, even one pwctl didn't make
    assert actions == [] and st.confirm == ("dev_delete_bus", "VOIP")
    assert "Delete bus 'VOIP'" in body(screen(st)[0])
    assert key(st, "y")[1] == [("dev_delete_bus", "VOIP")]
    st, actions = key(at(CH2_IN), "x")
    assert actions == [] and "hardware" in st.message


def test_apply_writes_without_confirm_and_R_restarts_with_warning():
    assert key(pc.UIState(tab="devices"), "v")[1] == [("dev_preview",)]
    st, actions = key(pc.UIState(tab="devices"), "A")
    assert actions == [("dev_apply",)] and st.confirm is None
    st, actions = key(pc.UIState(tab="devices"), "R")
    assert actions == [] and st.confirm == ("restart",)
    assert key(st, "y")[1] == [("restart_wireplumber",)]


def test_nothing_happens_until_devices_load():
    for k in ("d", "right", " ", "enter", "p", "h", "a", "x", "v", "A", "t", "down"):
        _, actions = pc.handle_key(pc.UIState(tab="devices"), k, CUR, [], devices=None)
        assert actions == []


# --- mouse ----------------------------------------------------------------------------------

def click(st, target, event="click"):
    lines, hits = screen(st)
    row, c0, _ = hit_for(hits, target)
    return pc.handle_mouse(st, hits, row, c0, event, CUR, [], devices=dv())


def test_buttons_land_on_labels():
    lines, hits = screen(pc.UIState(tab="devices"))
    for target, label in (("dev_default", "[Default]"), ("dev_mute", "[Mute]"), ("dev_label", "[Actions]"),
                          ("dev_profile", "[Profile]"), ("dev_add_bus", "[Add bus]"), ("dev_apply", "[Apply]")):
        r, c0, c1 = hit_for(hits, ("button", target))
        assert lines[r][0][c0:c1] == label
    _, actions = click(pc.UIState(tab="devices"), ("button", "dev_apply"))
    assert actions == [("dev_apply",)]


def test_rows_show_connections():
    links = pi.inventory(json.loads((FIX / "pwdump_links.json").read_text()))
    d = pc.devices_view(links, pcfg.default_config())
    text = body(pc.render_screen(pc.UIState(tab="devices"), CUR, None, [], 120, 50, devices=d)[0])
    assert "← Brave" in next(line for line in text.splitlines() if "GCS-8 CH USB" in line and " out " in line)
    assert "→ cinnamon" in next(line for line in text.splitlines() if "GCS-8 CH 2 " in line)


def test_legend_leads_with_enter_and_fits_80():
    lines, _ = screen(pc.UIState(tab="devices", message="hello"), width=80)
    text = body(lines)
    for k in ("↑↓ select", "Enter actions", "←→ volume", "m monitors", "H show hidden", "q quit"):
        assert k in text, k
    assert all(len(t.rstrip()) <= 80 for t, _ in lines)


# --- Enter-driven action menus --------------------------------------------------------------

def open_menu(target, devices=None):
    d = devices or dv()
    st, actions = key(at(target, d), "enter", devices=d)
    assert actions == [] and st.dev_menu is not None
    return st, d


def menu_labels(st, d):
    return [label for label, _ in pc.dev_menu_items(d, st)]


def pick(st, d, label):
    items = menu_labels(st, d)
    st = pc.dataclasses.replace(st, dev_menu_cursor=items.index(label))
    return key(st, "enter", devices=d)


def test_enter_on_a_hardware_output_opens_its_actions():
    st, d = open_menu(CH3_OUT)
    assert menu_labels(st, d) == ["Rename…", "Make default", "Mute", "Test sound", "Hide", "Back"]
    text = body(screen(st, d, height=60)[0])
    assert "Rename…" in text and "Make default" in text


def test_input_has_no_test_sound_and_muted_says_unmute():
    st, d = open_menu(CH2_IN)
    assert "Test sound" not in menu_labels(st, d)
    muted = pc.devices_view(pi.Inventory([pi.Card(1, "c", "Card", [], "on", [pi.Row(5, "n", "Mic", "in", "hardware",
                            card_id=1, mute=True)])], [], {"sink": None, "source": None}), pcfg.default_config())
    st2, _ = key(pc.UIState(tab="devices", dev_cursor=1), "enter", devices=muted)
    assert "Unmute" in menu_labels(st2, muted)


def test_menu_actions_map_to_the_same_commands():
    st, d = open_menu(CH2_IN)
    st2, actions = pick(st, d, "Make default")
    assert actions == [("dev_cmd", ["wpctl", "set-default", "96"])] and st2.dev_menu is None
    _, actions = pick(st, d, "Mute")
    assert actions == [("dev_cmd", ["wpctl", "set-mute", "96", "toggle"])]
    _, actions = pick(st, d, "Hide")
    assert actions == [("dev_hide", CH2_IN)]
    _, actions = pick(open_menu(CH3_OUT)[0], d, "Test sound")
    assert actions == [("dev_test", CH3_OUT)]


def test_rename_asks_for_the_label():
    st, d = open_menu(CH2_IN)
    _, actions = pick(st, d, "Rename…")
    assert actions == [("dev_rename", CH2_IN, "GCS-8 CH 2")]


def test_bus_menu_adds_delete_with_confirm():
    st, d = open_menu("VOIP")
    assert menu_labels(st, d)[-2:] == ["Delete bus", "Back"]
    st2, actions = pick(st, d, "Delete bus")
    assert actions == [] and st2.confirm == ("dev_delete_bus", "VOIP")


def test_pending_bus_menu():
    c = pcfg.add_bus(pcfg.default_config(), "OBS")
    d = dv(c, pending=c["buses"])
    st, _ = open_menu("OBS", d)
    assert menu_labels(st, d) == ["Create now", "Rename…", "Delete bus", "Back"]
    _, actions = pick(st, d, "Create now")
    assert actions == [("dev_bus_create", "OBS")]


def test_card_heading_lists_profiles_and_switches_after_confirm():
    st, d = open_menu("GCS-8")
    items = menu_labels(st, d)
    assert items[:4] == ["○ off", "● HiFi", "○ Direct", "○ pro-audio"] and items[-2:] == ["Hide card", "Back"]
    st2, actions = pick(st, d, "○ pro-audio")
    assert actions == [] and st2.confirm == ("dev_profile", 95, 3, "pro-audio")
    assert "Switch GCS-8 to pro-audio?" in body(screen(st2, d)[0])
    assert key(st2, "y", devices=d)[1] == [("dev_profile", 95, 3, "pro-audio")]


def test_picking_the_active_profile_just_closes():
    st, d = open_menu("GCS-8")
    st2, actions = pick(st, d, "● HiFi")
    assert actions == [] and st2.confirm is None and st2.dev_menu is None


def test_add_bus_row_at_the_end():
    entries = pc.dev_selectable(dv(), pc.UIState(tab="devices"))
    assert entries[-1] == ("addbus", None)
    st = pc.UIState(tab="devices", dev_cursor=len(entries) - 1)
    assert "+ Add bus…" in body(screen(st, height=60)[0])
    assert key(st, "enter")[1] == [("dev_prompt_bus",)]


def test_menu_navigation_and_closing():
    st, d = open_menu(CH3_OUT)
    st, _ = key(st, "down", "down", devices=d)
    assert st.dev_menu_cursor == 2
    st, _ = key(st, "up", devices=d)
    assert st.dev_menu_cursor == 1
    for k in ("esc", "left", "x"):
        st2, actions = key(st, k, devices=d)
        assert st2.dev_menu is None and actions == []
    _, actions = pick(st, d, "Back")
    assert actions == []


def test_menu_follows_the_row_not_a_stale_index():
    st, d = open_menu(CH3_OUT)
    assert st.dev_menu == ("row", CH3_OUT)


def test_click_on_a_menu_item_picks_it():
    st, d = open_menu(CH2_IN)
    lines, hits = screen(st, d, height=60)
    k = menu_labels(st, d).index("Make default")
    row, c0, _ = hit_for(hits, ("dev_menu_item", k))
    _, actions = pc.handle_mouse(st, hits, row, c0, "click", CUR, [], devices=d)
    assert actions == [("dev_cmd", ["wpctl", "set-default", "96"])]


def test_click_selects_double_click_opens_actions_wheel_moves():
    i = pc.dev_selectable(dv(), pc.UIState(tab="devices")).index(("row", INV.find(CH2_IN)))
    st, actions = click(pc.UIState(tab="devices"), ("device", i))
    assert st.dev_cursor == i and actions == []
    st, actions = click(pc.UIState(tab="devices"), ("device", i), "double")
    assert actions == [] and st.dev_menu == ("row", CH2_IN)
    st, _ = click(pc.UIState(tab="devices"), ("device", 0), "wheel_down")
    assert st.dev_cursor == 1


def test_rename_restart_question_warns_about_apps():
    st = pc.UIState(tab="devices", confirm=("restart_labels",))
    text = body(screen(st)[0])
    assert "Restart WirePlumber now" in text and "Discord" in text
    assert key(st, "y")[1] == [("restart_wireplumber",)]
