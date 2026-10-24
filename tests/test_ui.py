import re
from pathlib import Path

import pytest

import pwcore as pc

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
PRESETS = [{"name": "tracking", "label": "Low-latency tracking",
            "values": {"clock.force-rate": 48000, "clock.force-quantum": 64}},
           {"name": "mix", "label": "Mixing", "values": {"clock.force-quantum": 1024}}]


def block():
    p = pc.TopParser()
    for line in (FIX / "pwtop_batch.txt").read_text().splitlines():
        p.feed(line)
    return p.flush()


def UIState(**kw):
    """These tests are about Settings, Top and the chrome; pwctl itself opens on the Mixer."""
    kw.setdefault("tab", "settings")
    return pc.UIState(**kw)


def key(st, *keys, cur=CUR, presets=PRESETS):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, cur, presets)
        actions += a
    return st, actions


def screen(st, width=80, height=30):
    return pc.render_screen(st, CUR, block(), PRESETS, width, height)


def hit_for(hits, target):
    for row, c0, c1, t in hits:
        if t == target:
            return row, c0, c1
    raise AssertionError(f"no hit for {target}: {[h[3] for h in hits]}")


def text_at(lines, row, col):
    return lines[row][0][col]


# --- 5. view model -----------------------------------------------------------

def test_settings_render_shows_values_and_pending_marker():
    st = UIState()
    lines, _ = screen(st)
    body = "\n".join(t for t, _ in lines)
    assert "Force sample rate" in body and "48000" in body
    assert "Force buffer size" in body and "128" in body
    assert not any("*" in t for t, _ in lines if "◀" in t)  # no pending marker on any setting row
    st, _ = key(st, "down", "down", "down", "right")  # force-quantum 128 -> 256 pending
    lines, _ = screen(st)
    row = [t for t, _ in lines if "Force buffer size" in t][0]
    assert "256" in row and "*" in row


def test_settings_render_shows_allowed_rates_and_warning():
    st, _ = key(UIState(), "right")  # force-rate 48000 -> 88200 pending
    lines, _ = screen(st)
    body = "\n".join(t for t, _ in lines)
    assert "allowed rates: 48000" in body
    assert "not in allowed" in body


@pytest.mark.parametrize("width", [40, 80, 120])
def test_lines_fit_width(width):
    for tab in ("settings", "top"):
        lines, hits = screen(UIState(tab=tab), width=width)
        assert all(len(t) <= width for t, _ in lines)
        assert all(c1 <= width for _, _, c1, _ in hits)


def test_lines_fit_height():
    lines, _ = screen(UIState(tab="top"), height=10)
    assert len(lines) <= 10


def test_top_render_styles():
    lines, _ = pc.render_screen(UIState(tab="top"), CUR, block(), PRESETS, 200, 40)
    styles = {t.split()[-1] if t.split() else "": s for t, s in lines}
    assert any("QUANT" in t for t, _ in lines)
    assert styles["Dummy-Driver"] == "dim"
    assert styles["alsa_input.usb-Hercules_Rocksmith_USB_Guitar_Adapter-00.mono-fallback"] == "normal"
    assert styles["SYSTEM"] == "alert"  # the real capture has ERR=19 on SYSTEM
    hdr = (FIX / "pwtop_batch.txt").read_text().splitlines()[0]
    p = pc.TopParser()
    p.feed(hdr)
    p.feed("R  155    227  44100  24.6us  32.9us  0.01  0.01    2    S16LE 2 44100  + ALSA plug-in [sunvox]")
    lines, _ = pc.render_screen(UIState(tab="top"), CUR, p.flush(), PRESETS, 120, 40)
    assert [s for t, s in lines if "sunvox" in t] == ["alert"]


def test_top_render_waiting_for_data():
    lines, _ = pc.render_screen(UIState(tab="top"), CUR, None, PRESETS, 80, 20)
    assert any("waiting for pw-top" in t for t, _ in lines)


def test_presets_render_lists_labels():
    lines, _ = screen(UIState(tab="settings"), height=40)
    body = "\n".join(t for t, _ in lines)
    assert "Clock" in body and "Display" in body and "Presets" in body
    assert body.index("Clock") < body.index("Display") < body.index("Presets")
    assert "tracking" in body and "Low-latency tracking" in body and "buf 64" in body


def test_presets_render_empty_hint():
    lines, _ = pc.render_screen(UIState(tab="settings"), CUR, None, [], 80, 30)
    assert any("no presets" in t for t, _ in lines)


# --- 7. keys -----------------------------------------------------------------

def test_up_down_moves_cursor_and_clamps():
    st, _ = key(UIState(), "up")
    assert st.cursor == 0
    st, _ = key(st, *["down"] * 20)
    assert st.cursor == pc.PRESETS_ROW + len(PRESETS) - 1  # clock rows, Display, then the presets


def test_left_right_sets_pending_without_action():
    st, actions = key(UIState(), "down", "down", "down", "right", "right")
    assert st.pending == {"clock.force-quantum": 512}
    assert actions == []


def test_pending_back_to_current_clears():
    st, _ = key(UIState(), "down", "down", "down", "right", "left")
    assert st.pending == {}


def test_enter_applies_all_pending_in_order():
    st, actions = key(UIState(), "down", "down", "down", "right", "up", "up", "right", "enter")
    # cursor 3 (force buffer) -> up, up -> cursor 1 (min buffer) 32 -> 64
    assert actions == [("set_many", [("clock.min-quantum", 64), ("clock.force-quantum", 256)])]
    assert st.pending == {}


def test_enter_with_nothing_pending_does_nothing():
    _, actions = key(UIState(), "enter")
    assert actions == []


def test_enter_with_invalid_pending_blocks_and_reports():
    # min-quantum up to 4096 (> max 2048)
    st, actions = key(UIState(), "down", *["right"] * 7, "enter")
    assert st.pending == {"clock.min-quantum": 4096}
    assert actions == []
    assert "min" in st.message


def test_esc_reverts():
    st, _ = key(UIState(), "right", "esc")
    assert st.pending == {}


def test_tab_and_number_keys_switch_tabs():
    st = UIState(tab=pc.TABS[0])
    for want in pc.TABS[1:] + pc.TABS[:1]:
        st, _ = key(st, "tab")
        assert st.tab == want
    for n, tab in enumerate(pc.TABS, start=1):
        st, _ = key(st, str(n))
        assert st.tab == tab
    st, _ = key(st, str(len(pc.TABS) + 1))  # past the last tab: nothing
    assert st.tab == pc.TABS[-1]


def test_basic_tabs_come_first():
    assert pc.TABS.index("devices") < pc.TABS.index("patch") < pc.TABS.index("settings") < pc.TABS.index("top")
    assert "presets" not in pc.TABS


def test_restart_needs_confirmation():
    st, actions = key(UIState(), "R")
    assert actions == [] and st.confirm == ("restart",)
    st2, actions = key(st, "n")
    assert actions == [] and st2.confirm is None
    st3, actions = key(st, "y")
    assert actions == [("restart_wireplumber",)] and st3.confirm is None


def test_confirm_any_other_key_cancels_and_says_so():
    # A missed y/n prompt used to swallow every key, which looked like a frozen screen.
    for k in ("q", "down", "A", "x"):
        st, _ = key(UIState(), "R")
        st, actions = key(st, k)
        assert actions == [] and st.confirm is None and "cancelled" in st.message and "y" in st.message


def test_q_quits():
    assert key(UIState(), "q")[1] == [("quit",)]


def test_presets_keys():
    st = UIState(tab="settings", cursor=pc.PRESETS_ROW)
    st, actions = key(st, "down", "enter")
    assert actions == [("apply_preset", "mix")]
    _, actions = key(st, "s")
    assert actions == [("prompt_save",)]
    _, actions = key(st, "e")
    assert actions == [("prompt_label", "mix")]
    st, actions = key(st, "d")
    assert actions == [] and st.confirm == ("delete", "mix")
    st, actions = key(st, "y")
    assert actions == [("delete_preset", "mix")]


def test_presets_keys_with_no_presets():
    st = UIState(tab="settings", cursor=pc.PRESETS_ROW)
    _, actions = key(st, "enter", "d", "e", presets=[])
    assert actions == []
    _, actions = key(st, "s", presets=[])
    assert actions == [("prompt_save",)]


def test_top_scroll_keys():
    st, _ = key(UIState(tab="top"), "down", "down")
    assert st.top_scroll == 2
    st, _ = key(st, "up", "up", "up")
    assert st.top_scroll == 0


# --- 9. mouse ----------------------------------------------------------------

def click(st, hits, target, event="click", offset=0):
    row, c0, _ = hit_for(hits, target)
    return pc.handle_mouse(st, hits, row, c0 + offset, event, CUR, PRESETS)


@pytest.mark.parametrize("width", [80, 120])
def test_hitmap_lands_on_drawn_glyphs(width):
    lines, hits = screen(UIState(), width=width)
    for i in range(len(pc.SETTINGS)):
        r, c0, _ = hit_for(hits, ("arrow", i, -1))
        assert text_at(lines, r, c0) == "◀"
        r, c0, _ = hit_for(hits, ("arrow", i, +1))
        assert text_at(lines, r, c0) == "▶"
    r, c0, c1 = hit_for(hits, ("key", "enter"))
    assert lines[r][0][c0:c1] == " Enter "  # a chip's hit covers just the chip
    r, c0, c1 = hit_for(hits, ("tab", "top"))
    assert "Top" in lines[r][0][c0:c1]


def test_click_tab_switches():
    st = UIState()
    _, hits = screen(st)
    st, actions = click(st, hits, ("tab", "top"))
    assert st.tab == "top" and actions == []


def test_click_row_selects():
    st = UIState()
    _, hits = screen(st)
    st, _ = click(st, hits, ("setting", 3))
    assert st.cursor == 3 and st.pending == {}


def test_click_arrow_equals_keys():
    st = UIState()
    _, hits = screen(st)
    via_mouse, a1 = click(st, hits, ("arrow", 3, +1))
    via_keys, a2 = key(st, "down", "down", "down", "right")
    assert via_mouse.pending == via_keys.pending and via_mouse.cursor == via_keys.cursor
    assert a1 == a2 == []


def test_wheel_over_row_steps_value():
    st = UIState()
    _, hits = screen(st)
    st, _ = click(st, hits, ("setting", 3), event="wheel_up")
    assert st.pending == {"clock.force-quantum": 256}
    st, _ = click(st, hits, ("setting", 3), event="wheel_down")
    assert st.pending == {}


def test_apply_and_revert_chips():
    st, _ = key(UIState(), "down", "down", "down", "right")
    _, hits = screen(st)
    _, actions = click(st, hits, ("key", "enter"))
    assert actions == key(st, "enter")[1]
    st2, actions = click(st, hits, ("key", "esc"))
    assert st2.pending == {} and actions == []


def test_double_click_row_applies():
    st, _ = key(UIState(), "down", "down", "down", "right")
    _, hits = screen(st)
    _, actions = click(st, hits, ("setting", 3), event="double")
    assert actions == [("set_many", [("clock.force-quantum", 256)])]


def test_restart_chip_needs_confirmation():
    st = UIState()
    _, hits = screen(st)
    st, actions = click(st, hits, ("key", "R"))
    assert actions == [] and st.confirm == ("restart",)


def test_click_outside_targets_does_nothing():
    st = UIState(cursor=2)
    _, hits = screen(st)
    st2, actions = pc.handle_mouse(st, hits, 29, 79, "click", CUR, PRESETS)
    assert st2 == st and actions == []


def test_presets_mouse():
    st = UIState(tab="settings")
    _, hits = screen(st, height=40)
    st, actions = click(st, hits, ("preset", 1))
    assert st.cursor == pc.PRESETS_ROW + 1 and actions == []
    _, actions = click(st, hits, ("preset", 0), event="double")
    assert actions == [("apply_preset", "tracking")]
    _, hits = screen(st, height=40)  # a preset row's legend adds e and d
    _, actions = click(st, hits, ("key", "s"))
    assert actions == [("prompt_save",)]
    st2, actions = click(st, hits, ("key", "d"))
    assert actions == [] and st2.confirm == ("delete", "mix")


def test_wheel_scrolls_top():
    st = UIState(tab="top")
    _, hits = screen(st)
    st, _ = click(st, hits, ("toplist",), event="wheel_down")
    assert st.top_scroll == 1
    st, _ = click(st, hits, ("toplist",), event="wheel_up")
    assert st.top_scroll == 0


def test_click_while_confirming_cancels_without_acting():
    st, _ = key(UIState(), "R")
    _, hits = screen(st)
    st2, actions = click(st, hits, ("tab", "top"))
    assert st2.tab == "settings" and actions == [] and st2.confirm is None and "y confirms" in st2.message


# --- theme ------------------------------------------------------------------------------

def test_theme_row_switches_at_once_and_reports_it():
    st = UIState(cursor=pc.THEME_ROW)
    st, actions = key(st, "right")
    assert st.theme == "light" and actions == [("set_theme", "light")]
    assert "light" in "\n".join(t for t, _ in screen(st)[0])
    st, actions = key(st, "right", "right")
    assert st.theme == "dark"  # dark → light → crowbar → dark


def test_enter_on_the_theme_row_does_not_switch_it():
    st = UIState(cursor=pc.THEME_ROW)
    st, actions = key(st, "enter")
    assert st.theme == "dark" and actions == []


def test_every_theme_defines_every_role():
    roles = set(pc.THEMES["dark"])
    assert all(set(t) == roles for t in pc.THEMES.values())


def test_ui_file_round_trips_and_tolerates_junk(tmp_path):
    p = tmp_path / "pwctl" / "ui.json"
    assert pc.load_ui(p) == {"theme": "dark", "meter_fps": 10, "meter_fall": 20, "peak_hold": 1.5}
    pc.save_ui(p, {"theme": "light", "meter_fps": 20, "meter_fall": 80, "peak_hold": 3})
    assert pc.load_ui(p) == {"theme": "light", "meter_fps": 20, "meter_fall": 80, "peak_hold": 3}
    p.write_text("{not json")
    assert pc.load_ui(p) == {"theme": "dark", "meter_fps": 10, "meter_fall": 20, "peak_hold": 1.5}
    p.write_text('{"theme": "neon", "meter_fps": 120}')
    assert pc.load_ui(p) == {"theme": "dark", "meter_fps": 10, "meter_fall": 20, "peak_hold": 1.5}


def test_failed_messages_are_red_and_others_green():
    st = UIState(message="linked")
    assert screen(st)[0][-1][1] == "ok"
    st = UIState(message="boom", failed="boom")
    assert screen(st)[0][-1][1] == "alert"
    st.message = "linked"  # a later message is not tarred by the old failure
    assert screen(st)[0][-1][1] == "ok"


def test_black_text_is_never_bold():
    """Bold black renders grey in most terminals."""
    for theme in pc.THEMES.values():
        for fg, _, attr in theme.values():
            assert not (fg in ("black", "#000000") and "bold" in attr)


def test_nearest_256_picks_the_cube_or_the_grey_ramp():
    assert pc.nearest_256("#ff8700") == 208
    assert pc.nearest_256("#000000") == 16
    assert pc.nearest_256("#808080") == 244
    assert 232 <= pc.nearest_256("#2e2e33") <= 255


def test_crowbar_theme_is_mint_y_dark_orange():
    o = pc.THEMES["crowbar"]
    assert o["selected"][1] == "#ff7139" and o["screen"][1] == "#2e2e33"
    assert "crowbar" in pc.THEME_NAMES and "orange" not in pc.THEMES


def test_saved_orange_loads_as_crowbar(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text('{"theme": "orange"}')
    assert pc.load_ui(p)["theme"] == "crowbar"


# --- no button rows; the legend's chips take the mouse --------------------------------------

@pytest.mark.parametrize("tab", ["settings", "devices", "patch"])
def test_no_button_rows(tab):
    lines, hits = screen(UIState(tab=tab))
    assert not any(t[0] == "button" for *_, t in hits)
    assert not any(re.search(r"\[[A-Z][a-z]+( [a-z]+)*\]", text) for text, _ in lines[1:])


def test_multi_key_chips_have_no_hit():
    _, hits = screen(UIState())
    keys = {t[1] for *_, t in hits if t[0] == "key"}
    assert keys == {"enter", "esc", "s", "R", "q"} | {"*", "/", "<", ">", "-", "+"}  # not ↑↓, ←→ or Tab/1-n


def test_meter_frame_rate_steps_and_saves():
    st = UIState(cursor=pc.FPS_ROW)
    st, actions = key(st, "right")
    assert st.meter_fps == 15 and actions == [("save_ui",)]
    st, _ = key(st, *["right"] * 8)
    assert st.meter_fps == 60  # stops at the top
    st, _ = key(st, *["left"] * 8)
    assert st.meter_fps == 5
    text = "\n".join(t for t, _ in screen(st, height=40)[0])
    assert "Meter frame rate" in text and "5 fps" in text
    assert text.index("Theme") < text.index("Meter frame rate") < text.index("Presets")


def test_meter_fall_steps_and_saves():
    st = UIState(cursor=pc.FALL_ROW)
    st, actions = key(st, "right")
    assert st.meter_fall == 40 and actions == [("save_ui",)]
    st, _ = key(st, *["left"] * 6)
    assert st.meter_fall == 10
    assert pc.ui_prefs(st) == {"theme": "dark", "meter_fps": 10, "meter_fall": 10, "peak_hold": 1.5}
    text = "\n".join(t for t, _ in screen(st, height=40)[0])
    assert "Meter fall" in text and "10 dB/s" in text
    assert text.index("Meter frame rate") < text.index("Meter fall") < text.index("Peak hold") < text.index("Presets")


def test_peak_hold_steps_to_off_and_saves():
    st = UIState(cursor=pc.HOLD_ROW)
    st, actions = key(st, "right")
    assert st.peak_hold == 2 and actions == [("save_ui",)]
    st, _ = key(st, *["left"] * 8)
    assert st.peak_hold == 0
    assert "Peak hold" in "\n".join(t for t, _ in screen(st, height=40)[0]) and "off" in "\n".join(
        t for t, _ in screen(st, height=40)[0])
