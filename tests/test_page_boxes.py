"""UI conventions QA, 2026-10-26: Mixer, Devices and Top get Audio's treatment, boxes and every line a row
(Skrappjaw: "the work that made the audio settings page work with box separation was the right move. Along with
every item navigable. I think mixer devices and top need the same treatment."). Plan: notes/qa-boxes-plan.md."""
import pwcore as pc

from test_legends import CUR, DEV, PATCH
from test_mixer import MX


def screen(st, block=None, height=80, width=100):
    return pc.render_screen(st, CUR, block or [], width, height, devices=DEV, patch=PATCH, mixer=MX)


def body(lines):
    end = next(i for i, (t, _) in enumerate(lines) if t.startswith("┌─ Keys"))
    return lines[2:end]


def titles(lines):
    return [t.split("─ ", 1)[1].split(" ─")[0] for t, _ in body(lines) if t.startswith("┌─ ")]


def role_at(style, col):
    base, spans = style if isinstance(style, tuple) else (style, [])
    return next((r for a, b, r in reversed(spans) if a <= col < b), base)


# --- Mixer: two boxes, Apps a heading inside each -----------------------------------------------------------

def test_mixer_is_a_playback_box_and_a_recording_box():
    lines, _ = screen(pc.UIState(tab="mixer"))
    assert titles(lines) == ["Playback", "Recording"]
    assert all(t.startswith(("┌", "│", "└")) for t, _ in body(lines) if t.strip())


def test_mixer_apps_heading_is_title_coloured_inside_its_box():
    lines, _ = screen(pc.UIState(tab="mixer"))
    apps = [(t, s) for t, s in body(lines) if t.startswith("│") and t[1:].strip().startswith("Apps")]
    assert apps and all(role_at(s, t.index("Apps")) == "title" for t, s in apps)


def test_mixer_rows_still_select_and_the_cursor_scrolls_into_view():
    items = pc.mixer_selectable(MX)
    st = pc.UIState(tab="mixer", mix_cursor=len(items) - 1)
    lines, hits = screen(st, height=24)
    rows = {h[0] for h in hits if h[3] == ("mix_row", len(items) - 1)}
    assert rows and any(lines[r][0].startswith("│ > ") for r in rows)


# --- Devices: a box per card, every channel a row -----------------------------------------------------------

def dev_items(**kw):
    return pc.dev_selectable(DEV, pc.UIState(tab="devices", **kw))


def test_devices_has_a_box_per_card_then_buses_and_off():
    lines, _ = screen(pc.UIState(tab="devices"))
    names = titles(lines)
    cards = [c.label for c in DEV["inv"].cards if c.active != "off"]
    assert names[:len(cards)] == cards and "Buses" in names and "Virtual" not in names
    assert all(t.startswith(("┌", "│", "└")) for t, _ in body(lines) if t.strip())


def test_every_channel_is_a_row_with_its_own_note():
    items = dev_items()
    chans = [i for i, (k, _) in enumerate(items) if k == "chan"]
    assert chans
    for i in chans:
        text = screen(pc.UIState(tab="devices", dev_cursor=i))[0][-1][0]
        assert "on the Mixer (1)" in text and text.strip(), i


def test_enter_on_a_channel_does_nothing():
    i = next(n for n, (k, _) in enumerate(dev_items()) if k == "chan")
    st, actions = pc.handle_key(pc.UIState(tab="devices", dev_cursor=i), "enter", CUR, DEV)
    assert actions == [] and not st.dev_menu


def test_a_card_box_starts_with_its_profile_row_and_column_headers():
    lines, hits = screen(pc.UIState(tab="devices", dev_cursor=0))
    text = [t for t, _ in lines]
    top = next(n for n, t in enumerate(text) if t.startswith("┌─ "))
    assert text[top + 1].startswith("│ > Profile") and "Dir" in text[top + 2] and "Status" in text[top + 2]
    assert role_at(lines[top + 2][1], text[top + 2].index("Dir")) == "title"


# --- Top: a box per driver, every line a row -----------------------------------------------------------------

def top_block():
    from pathlib import Path
    p = pc.TopParser()
    for line in (Path(__file__).parent / "fixtures" / "pwtop_batch.txt").read_text().splitlines():
        p.feed(line)
    return p.flush()


def test_top_has_a_box_per_running_driver_then_idle():
    block = top_block()
    lines, _ = screen(pc.UIState(tab="top"), block=block)
    names = titles(lines)
    drivers = [r for r in block if not r.follower and r.state == "R"]
    assert len(names) == len(drivers) + 1 and names[-1] == "Idle"
    assert names[0].startswith("alsa_input.usb-BOSS_GCS-8") and "128 @ 48k" in names[0]


def test_every_top_line_is_a_row_with_a_note():
    block = top_block()
    for i in range(len(block)):
        lines, hits = screen(pc.UIState(tab="top", top_scroll=i), block=block, height=60)
        assert [h for h in hits if h[3] == ("top_row", i)], i
        assert lines[-1][0].strip() and "QUANT" not in lines[-1][0], i
    st = pc.UIState(tab="top", top_scroll=0)
    first = screen(st, block=block)[0][-1][0]
    assert "driver" in first or "idle" in first


def test_top_cursor_clamps_to_the_last_row():
    block = top_block()
    st = pc.UIState(tab="top", top_scroll=500)
    lines, _ = screen(st, block=block, height=24)
    assert st.top_scroll == len(block) - 1 and any(t.startswith("│ > ") for t, _ in lines)


# --- Preferences: Display and Key Bindings in boxes (Skrappjaw: "add preferences ... box up the sections") --------

def prefs_pane(st):
    return [t.split("│", 1)[1] for t, _ in screen(st, height=80)[0] if "│" in t]


def test_preferences_sections_are_boxes_with_view_headings_inside():
    pane = prefs_pane(pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane"))
    tops = [t.split("─ ", 1)[1].split(" ─")[0] for t in pane if t.startswith("┌─ ")]
    assert tops == ["Display", "Key Bindings"]
    inside = pane[next(i for i, t in enumerate(pane) if t.startswith("┌─ Key Bindings")):]
    assert any(t.startswith("│   Global") for t in inside) and any("Reset all keys" in t for t in inside)


def test_preferences_rows_still_select_inside_their_boxes():
    st = pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane", cursor=pc.RESET_KEYS_ROW)
    assert any(t.startswith("│ > Reset all keys") for t in prefs_pane(st))
