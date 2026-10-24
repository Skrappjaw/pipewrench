"""Channel work on the Mixer, hardware on Devices (Skrappjaw, 2026-10-24: "all the work is channel wise").
Mixer rows rename and jump to their card's profile; Devices only selects cards, buses and Add bus, and
lists channels read-only under each card. Hiding is card-level, on Devices."""
import json
from pathlib import Path

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
DUMP = json.loads((FIX / "pwdump_inventory.json").read_text())
INV = pi.inventory(DUMP)
CH2_IN = "alsa_input.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_in_GCS8_0_6_7__source"


def mx(config=None):
    return pc.mixer_view(INV, config or pcfg.default_config(), pi.streams(DUMP))


def dv(config=None, **kw):
    return pc.devices_view(INV, config or pcfg.default_config(), **kw)


def mix_at(label, section="playback", m=None):
    m = m or mx()
    i = next(n for n, (_, sec, r) in enumerate(pc.mixer_selectable(m)) if r.label == label and sec == section)
    return pc.UIState(tab="mixer", mix_cursor=i)


def mkey(st, *keys, m=None):
    m, actions = m or mx(), []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, [], mixer=m)
        actions += a
    return st, actions


def dkey(st, *keys, devices=None):
    devices, actions = devices or dv(), []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, [], devices=devices)
        actions += a
    return st, actions


def mix_labels(st, m=None):
    return [label for label, _ in pc.mix_menu_items(m or mx(), st)]


def pick(st, label, m=None):
    """Open the Mixer row menu and choose an item by label."""
    st, _ = mkey(st, "enter", m=m)
    st.mix_menu_cursor = mix_labels(st, m).index(label)
    return mkey(st, "enter", m=m)


# --- Mixer --------------------------------------------------------------------

def test_mixer_device_menu_has_rename_and_card_profile():
    st, _ = mkey(mix_at("GCS-8 CH USB"), "enter")
    assert mix_labels(st) == ["Set as system default", "Mute", "Test sound", "Rename…", "Card profile…", "Back"]


def test_mixer_recording_menu():
    st, _ = mkey(mix_at("GCS-8 CH 2", "recording"), "enter")
    assert mix_labels(st) == ["★ System default", "Mute", "Rename…", "Card profile…", "Back"]


def test_mixer_bus_has_rename_but_no_card_profile():
    st, _ = mkey(mix_at("MUSIC Audio/Duplex sink"), "enter")
    labels = mix_labels(st)
    assert "Rename…" in labels and "Card profile…" not in labels


def test_no_device_settings_item_any_more():
    st, _ = mkey(mix_at("GCS-8 CH USB"), "enter")
    assert "Device settings…" not in mix_labels(st)


def test_enter_enter_still_sets_default():
    _, actions = mkey(mix_at("GCS-8 CH USB"), "enter", "enter")
    assert actions and actions[0][0] == "mix_cmd" and actions[0][1][1] == "set-default"


def test_mixer_rename_emits_dev_rename():
    _, actions = pick(mix_at("GCS-8 CH 2", "recording"), "Rename…")
    assert actions == [("dev_rename", CH2_IN, "GCS-8 CH 2")]


def test_card_profile_jumps_to_card_with_menu_open():
    st, actions = pick(mix_at("GCS-8 CH 2", "recording"), "Card profile…")
    assert actions == [] and st.tab == "devices" and st.dev_focus == ("card", 95)
    st = pc.focus_device(st, dv())
    kind, obj = pc.dev_selectable(dv(), st)[st.dev_cursor]
    assert kind == "card" and obj.id == 95
    assert st.dev_menu == ("card", 95)
    assert "Hide card" in [label for label, _ in pc.dev_menu_items(dv(), st)]


# --- Devices --------------------------------------------------------------------

def test_cursor_skips_channels():
    kinds = {kind for kind, _ in pc.dev_selectable(dv(), pc.UIState(tab="devices"))}
    assert kinds <= {"card", "row", "pending", "addbus"}
    rows = [obj for kind, obj in pc.dev_selectable(dv(), pc.UIState(tab="devices")) if kind == "row"]
    assert rows and all(r.kind == "virtual" for r in rows)


def test_monitors_are_read_only_too():
    st = pc.UIState(tab="devices", dev_monitors=True)
    rows = [obj for kind, obj in pc.dev_selectable(dv(), st) if kind == "row"]
    assert all(r.kind == "virtual" for r in rows)


def test_channels_still_listed_dim():
    lines, hits = pc.render_screen(pc.UIState(tab="devices"), CUR, None, [], 120, 80, devices=dv())
    row = next((t, s) for t, s in lines if "GCS-8 CH 2 " in t or t.rstrip().endswith("GCS-8 CH 2"))
    assert row[1] == "dim"
    n = next(i for i, (t, _) in enumerate(lines) if t == row[0])
    assert not [h for h in hits if h[0] == n]


def test_bus_menu_is_hide_and_delete():
    st = pc.UIState(tab="devices")
    i = next(n for n, (k, o) in enumerate(pc.dev_selectable(dv(), st)) if k == "row" and o.name == "MUSIC")
    st, _ = dkey(pc.UIState(tab="devices", dev_cursor=i), "enter")
    assert [label for label, _ in pc.dev_menu_items(dv(), st)] == ["Hide", "Delete bus", "Back"]


def test_card_menu_offers_unhide_channels_from_old_configs():
    c = pcfg.toggle_hidden(pcfg.default_config(), CH2_IN)
    d = dv(c)
    st = pc.UIState(tab="devices", dev_focus=("card", 95))
    st = pc.focus_device(st, d)
    labels = [label for label, _ in pc.dev_menu_items(d, st)]
    assert "Unhide 1 channel" in labels
    st.dev_menu_cursor = labels.index("Unhide 1 channel")
    _, actions = dkey(st, "enter", devices=d)
    assert actions == [("dev_hide", CH2_IN)]  # toggles it back


def test_card_menu_has_no_unhide_channels_normally():
    st = pc.focus_device(pc.UIState(tab="devices", dev_focus=("card", 95)), dv())
    assert not any(label.startswith("Unhide ") and "channel" in label for label, _ in pc.dev_menu_items(dv(), st))


def test_h_hides_a_card():
    st = pc.focus_device(pc.UIState(tab="devices", dev_focus=("card", 193)), dv())
    st.dev_menu = None
    _, actions = dkey(st, "h")
    assert actions == [("dev_hide", next(c.name for c in INV.cards if c.id == 193))]


def test_d_points_to_the_mixer():
    st, _ = dkey(pc.UIState(tab="devices"), "d")
    assert st.message == "default and rename are on the Mixer (1)"
