import re
from pathlib import Path

import pytest

import pwcore as pc

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())


def block():
    p = pc.TopParser()
    for line in (FIX / "pwtop_batch.txt").read_text().splitlines():
        p.feed(line)
    return p.flush()


def UIState(**kw):
    """These tests are about Settings, Top and the chrome; PipeWrench itself opens on the Mixer. Settings
    opens with its menu in focus; most tests here start in the open category."""
    kw.setdefault("tab", "settings")
    kw.setdefault("settings_focus", "pane")
    return pc.UIState(**kw)


def key(st, *keys, cur=CUR):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, cur)
        actions += a
    return st, actions


def screen(st, width=80, height=30):
    return pc.render_screen(st, CUR, block(), width, height)


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
    assert "isn't in allowed" in body


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
    lines, _ = pc.render_screen(UIState(tab="top"), CUR, block(), 200, 40)
    styles = {t.split()[-1] if t.split() else "": s for t, s in lines}
    assert any("QUANT" in t for t, _ in lines)
    assert styles["Dummy-Driver"] == "dim"
    assert styles["alsa_input.usb-Hercules_Rocksmith_USB_Guitar_Adapter-00.mono-fallback"] == "normal"
    assert styles["SYSTEM"] == "alert"  # the real capture has ERR=19 on SYSTEM
    hdr = (FIX / "pwtop_batch.txt").read_text().splitlines()[0]
    p = pc.TopParser()
    p.feed(hdr)
    p.feed("R  155    227  44100  24.6us  32.9us  0.01  0.01    2    S16LE 2 44100  + ALSA plug-in [sunvox]")
    lines, _ = pc.render_screen(UIState(tab="top"), CUR, p.flush(), 120, 40)
    assert [s for t, s in lines if "sunvox" in t] == ["alert"]


def test_top_render_waiting_for_data():
    lines, _ = pc.render_screen(UIState(tab="top"), CUR, None, 80, 20)
    assert any("waiting for pw-top" in t for t, _ in lines)








# --- 7. keys -----------------------------------------------------------------

def test_up_down_moves_cursor_and_clamps():
    st, _ = key(UIState(), "up")
    assert st.cursor == 0
    st, _ = key(st, *["down"] * 60)
    assert st.cursor == pc.CLOCK_BUTTONS_ROW  # Audio: the clock rows, then Apply · Save · Reset
    st, _ = key(UIState(settings_cat="prefs"), *["down"] * 60)
    assert st.cursor == pc.RESET_KEYS_ROW  # Preferences: Display, then Keys


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






# --- Settings: the category menu, and the clock's Apply · Save · Reset -------------------------

def test_settings_opens_on_the_menu_and_arrows_pick_a_category():
    st = pc.UIState(tab="settings")
    assert st.settings_focus == "menu" and st.settings_cat == "audio"
    st, actions = key(st, "down")
    assert st.settings_cat == "prefs" and st.cursor == 0 and actions == []
    st, _ = key(st, "down", "up")
    assert st.settings_cat == "audio"
    st, _ = key(st, "right")
    assert st.settings_focus == "pane"
    st, _ = key(pc.UIState(tab="settings"), "enter")
    assert st.settings_focus == "pane"


def test_menu_keys_never_change_a_value():
    st, actions = key(pc.UIState(tab="settings"), "left", "up", "down", "up")
    assert actions == [] and st.pending == {}


def test_esc_drops_pending_first_then_goes_back_to_the_menu():
    st, _ = key(UIState(), "right")
    assert st.pending
    st, _ = key(st, "esc")
    assert st.pending == {} and st.settings_focus == "pane"
    st, _ = key(st, "esc")
    assert st.settings_focus == "menu"


def test_the_pane_shows_only_the_open_category():
    audio = "\n".join(t for t, _ in screen(UIState(), height=40)[0])
    assert "Clock" in audio and "Realtime" in audio and "Display" not in audio and "Keys  " not in audio
    prefs = "\n".join(t for t, _ in screen(UIState(settings_cat="prefs"), height=40)[0])
    assert "Display" in prefs and "Theme" in prefs and "Force sample rate" not in prefs
    assert "Audio" in prefs and "Preferences" in prefs  # the menu stays on the left
    assert "preset" not in (audio + prefs).lower()


def test_menu_marks_the_open_category():
    lines, _ = screen(pc.UIState(tab="settings", settings_cat="prefs"))
    row = next(n for n, (t, _) in enumerate(lines) if t.startswith(" Preferences"))
    assert (0, pc.MENU_W - 1, "selected") in lines[row][1][1]
    lines, _ = screen(UIState(settings_cat="prefs"))  # keys in the pane: the menu keeps an anchor
    assert (0, pc.MENU_W - 1, "anchor") in lines[row][1][1]


def test_clicking_a_category_opens_it_and_a_row_focuses_the_pane():
    st = pc.UIState(tab="settings")
    lines, hits = screen(st)
    st, _ = click(st, hits, ("settings_cat", "prefs"))
    assert st.settings_cat == "prefs" and st.settings_focus == "menu"
    lines, hits = screen(st)
    st, _ = click(st, hits, ("setting", pc.FPS_ROW))
    assert st.settings_focus == "pane" and st.cursor == pc.FPS_ROW


def test_clock_buttons_apply_save_reset():
    st = UIState(cursor=pc.CLOCK_BUTTONS_ROW, pending={"clock.force-quantum": 256})
    _, actions = key(st, "enter")  # Apply is first
    assert actions == [("set_many", [("clock.force-quantum", 256)])]
    st2, actions = key(st, "right", "enter")  # Save, with an edit not yet applied
    assert actions == [] and "apply first" in st2.message and st2.failed == st2.message
    _, actions = key(UIState(cursor=pc.CLOCK_BUTTONS_ROW), "right", "enter")
    assert actions == [("save_clock",)]
    st, actions = key(UIState(cursor=pc.CLOCK_BUTTONS_ROW), "right", "right", "right", "enter")  # Reset, clamped
    assert actions == [] and st.clock_button == 2


def test_s_saves_from_anywhere_in_settings():
    assert key(pc.UIState(tab="settings"), "s")[1] == [("save_clock",)]
    assert key(UIState(settings_cat="prefs"), "s")[1] == [("save_clock",)]


def test_reset_loads_pipewire_defaults_as_pending():
    cur = {**CUR, "clock.force-rate": 96000, "clock.force-quantum": 128, "clock.min-quantum": 64,
           "clock.max-quantum": 2048}
    st, actions = key(UIState(cursor=pc.CLOCK_BUTTONS_ROW, clock_button=2), "enter", cur=cur)
    assert actions == []
    assert st.pending == {"clock.force-rate": 0, "clock.force-quantum": 0, "clock.min-quantum": 32}
    assert "Apply" in st.message
    _, actions = key(st, "left", "left", "enter", cur=cur)  # then Apply writes them
    assert [k for k, _ in actions[0][1]] == ["clock.min-quantum", "clock.force-rate", "clock.force-quantum"]


def test_reset_at_the_defaults_says_so():
    cur = {**CUR, **pc.CLOCK_DEFAULTS}
    st, _ = key(UIState(cursor=pc.CLOCK_BUTTONS_ROW, clock_button=2), "enter", cur=cur)
    assert st.pending == {} and "already" in st.message


def test_clock_button_click_presses_it():
    st = UIState()
    lines, hits = screen(st, height=40)
    row, c0, c1 = hit_for(hits, ("clock_button", 1))
    assert "Save" in lines[row][0][c0:c1]
    _, actions = click(st, hits, ("clock_button", 1))
    assert actions == [("save_clock",)]


def test_clock_buttons_light_only_under_the_cursor():
    """chip is for key legends only; a button looks lit only when the cursor is on it (Skrappjaw, 2026-10-25)."""
    def roles(st):
        lines, hits = screen(st, height=40)
        row, _, _ = hit_for(hits, ("clock_button", 0))
        text, style = lines[row]
        spans = style[1] if isinstance(style, tuple) else []
        on_top = lambda col: ([r for a, b, r in spans if a <= col < b] or ["normal"])[-1]  # later spans draw over
        return [on_top(text.index(f"[ {name} ]")) for name in pc.CLOCK_BUTTONS]

    assert roles(UIState(cursor=0)) == ["normal"] * 3
    assert roles(UIState(cursor=pc.CLOCK_BUTTONS_ROW, clock_button=1)) == ["normal", "selected", "normal"]


def test_top_scroll_keys():
    st, _ = key(UIState(tab="top"), "down", "down")
    assert st.top_scroll == 2
    st, _ = key(st, "up", "up", "up")
    assert st.top_scroll == 0


# --- 9. mouse ----------------------------------------------------------------

def click(st, hits, target, event="click", offset=0):
    row, c0, _ = hit_for(hits, target)
    return pc.handle_mouse(st, hits, row, c0 + offset, event, CUR)


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


def test_wheel_scrolls_settings_and_never_changes_a_value():
    # Skrappjaw, 2026-10-24: the mouse wheel scrolls everywhere.
    st = UIState(cursor=1)
    _, hits = screen(st)
    st, actions = click(st, hits, ("setting", 3), event="wheel_down")
    assert st.cursor == 2 and st.pending == {} and actions == []   # from the selection, not the mouse
    st, _ = click(st, hits, ("setting", 3), event="wheel_up")
    assert st.cursor == 1 and st.pending == {}


def test_wheel_works_anywhere_on_the_screen():
    st = UIState(cursor=1)
    lines, hits = screen(st)
    blank = next(n for n, (t, _) in enumerate(lines) if not t.strip())
    for y in (0, blank):                                    # the tab bar, an empty line
        st2, _ = pc.handle_mouse(st, hits, y, 2, "wheel_down", CUR)
        assert st2.cursor == 2 and st2.tab == "settings"


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
    st2, actions = pc.handle_mouse(st, hits, 29, 79, "click", CUR)
    assert st2 == st and actions == []




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
    st = UIState(settings_cat="prefs", cursor=pc.THEME_ROW)
    st, actions = key(st, "right")
    assert st.theme == "light" and actions == [("set_theme", "light")]
    assert "light" in "\n".join(t for t, _ in screen(st)[0])
    st, actions = key(st, *["right"] * (len(pc.THEME_NAMES) - 1))
    assert st.theme == "dark"  # the list wraps round
    assert pc.THEME_NAMES == ["dark", "light", "crowbar", "pipeboy", "pipeboy-amber", "pipeboy-cyan",
                              "pipeboy-white", "vaporware", "pipecraft", "pipeos-lite", "pipeos-dark"]


def _rgb(colour):
    return pc.hex_rgb(colour)


def test_pipe_boy_is_monochrome_green():
    t = pc.THEMES["pipeboy"]
    assert "pipeboy" in pc.THEME_NAMES and pc.THEME_MIN_COLOURS["pipeboy"] == 256
    hexes = {c for fg, bg, _ in t.values() for c in (fg, bg) if c}
    assert hexes and all(c.startswith("#") for c in hexes)
    for c in hexes:  # every colour is a green: green channel leads, red and blue trail it
        r, g, b = _rgb(c)
        assert g >= r and g >= b and g > 0, c
    # without hue, status reads by style: ok bold, pending underlined, alert a block
    assert "bold" in t["ok"][2] and "underline" in t["pending"][2] and t["alert"][1]
    assert t["selected"] != t["alert"] and t["selected"] != t["chip"]


def _hsv(c):
    import colorsys
    return colorsys.rgb_to_hsv(*(v / 255 for v in pc.hex_rgb(c)))


@pytest.mark.parametrize("name,hue", [("pipeboy", 0.37), ("pipeboy-amber", 0.11), ("pipeboy-cyan", 0.51),
                                      ("pipeboy-white", None)])
def test_each_pipe_boy_is_one_hue(name, hue):
    t = pc.THEMES[name]
    assert pc.THEME_MIN_COLOURS[name] == 256
    hexes = {c for fg, bg, _ in t.values() for c in (fg, bg) if c}
    for c in hexes:
        h, sat, _ = _hsv(c)
        if hue is None:
            assert sat < 0.1, c                      # white: greys only
        else:
            assert abs(h - hue) < 0.04, (c, h)      # one hue, many brightnesses
    # every Pipe Boy shares the same layout of roles; only the hue changes
    assert {r: (fg is None, bg is None, a) for r, (fg, bg, a) in t.items()} == \
           {r: (fg is None, bg is None, a) for r, (fg, bg, a) in pc.THEMES["pipeboy"].items()}


def test_vaporware_has_vaporwave_colours():
    t = pc.THEMES["vaporware"]
    assert pc.THEME_MIN_COLOURS["vaporware"] == 256 and pc.theme_label("vaporware") == "Vaporware"
    assert t["title"][0] == "#ff71ce" and t["selected"][1] == "#01cdfe" and t["ok"][0] == "#05ffa1"
    assert len({t["ok"][0], t["pending"][0], t["alert"][0]}) == 3


def test_theme_labels():
    assert [pc.theme_label(n) for n in pc.THEME_NAMES] == ["dark", "light", "crowbar", "Pipe Boy", "Pipe Boy Amber",
                                                           "Pipe Boy Cyan", "Pipe Boy White", "Vaporware",
                                                           "Pipecraft", "pipeOS-lite", "pipeOS-dark"]


def test_pipecraft_uses_minecraft_text_colours():
    t = pc.THEMES["pipecraft"]
    assert pc.THEME_MIN_COLOURS["pipecraft"] == 256
    # Minecraft's formatting-code colours: yellow, green, gold, red, grey
    assert (t["title"][0], t["ok"][0], t["pending"][0], t["alert"][0], t["dim"][0]) == \
           ("#ffff55", "#55ff55", "#ffaa00", "#ff5555", "#aaaaaa")


def test_pipe_boy_shows_its_name():
    st = UIState(settings_cat="prefs", cursor=pc.THEME_ROW, theme="pipeboy")
    assert "Pipe Boy" in "\n".join(t for t, _ in screen(st)[0])
    assert pc.theme_label("pipeboy") == "Pipe Boy" and pc.theme_label("dark") == "dark"


def test_enter_on_the_theme_row_does_not_switch_it():
    st = UIState(settings_cat="prefs", cursor=pc.THEME_ROW)
    st, actions = key(st, "enter")
    assert st.theme == "dark" and actions == []


def test_every_theme_defines_every_role():
    roles = set(pc.THEMES["dark"])
    assert all(set(t) == roles for t in pc.THEMES.values())


def test_ui_file_round_trips_and_tolerates_junk(tmp_path):
    p = tmp_path / "pipewrench" / "ui.json"
    assert pc.load_ui(p) == {"theme": "dark", "meter_fps": 10, "meter_fall": 20, "peak_hold": 1.5, "volume_bar": True, "keys": {}}
    pc.save_ui(p, {"theme": "light", "meter_fps": 20, "meter_fall": 80, "peak_hold": 3, "volume_bar": False})
    assert pc.load_ui(p) == {"theme": "light", "meter_fps": 20, "meter_fall": 80, "peak_hold": 3, "volume_bar": False, "keys": {}}
    p.write_text("{not json")
    assert pc.load_ui(p) == {"theme": "dark", "meter_fps": 10, "meter_fall": 20, "peak_hold": 1.5, "volume_bar": True, "keys": {}}
    p.write_text('{"theme": "neon", "meter_fps": 120}')
    assert pc.load_ui(p) == {"theme": "dark", "meter_fps": 10, "meter_fall": 20, "peak_hold": 1.5, "volume_bar": True, "keys": {}}


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
    assert keys == {"enter", "esc", "s", "R", "q"} | {"*", "<", ">", "-", "+"}  # not ↑↓, ←→ or Tab/1-n


def test_meter_frame_rate_steps_and_saves():
    st = UIState(settings_cat="prefs", cursor=pc.FPS_ROW)
    st, actions = key(st, "right")
    assert st.meter_fps == 15 and actions == [("save_ui",)]
    st, _ = key(st, *["right"] * 8)
    assert st.meter_fps == 60  # stops at the top
    st, _ = key(st, *["left"] * 8)
    assert st.meter_fps == 5
    text = "\n".join(t for t, _ in screen(st, height=80)[0])
    assert "Meter frame rate" in text and "5 fps" in text
    assert text.index("Theme") < text.index("Meter frame rate") < text.index("  Keys")


def test_meter_fall_steps_and_saves():
    st = UIState(settings_cat="prefs", cursor=pc.FALL_ROW)
    st, actions = key(st, "right")
    assert st.meter_fall == 40 and actions == [("save_ui",)]
    st, _ = key(st, *["left"] * 6)
    assert st.meter_fall == 10
    assert pc.ui_prefs(st) == {"theme": "dark", "meter_fps": 10, "meter_fall": 10, "peak_hold": 1.5, "volume_bar": True, "keys": {}}
    text = "\n".join(t for t, _ in screen(st, height=80)[0])
    assert "Meter fall" in text and "10 dB/s" in text
    assert text.index("Meter frame rate") < text.index("Meter fall") < text.index("Peak hold") < text.index("  Keys")


def test_peak_hold_steps_to_off_and_saves():
    st = UIState(settings_cat="prefs", cursor=pc.HOLD_ROW)
    st, actions = key(st, "right")
    assert st.peak_hold == 2 and actions == [("save_ui",)]
    st, _ = key(st, *["left"] * 8)
    assert st.peak_hold == 0
    assert "Peak hold" in "\n".join(t for t, _ in screen(st, height=40)[0]) and "off" in "\n".join(
        t for t, _ in screen(st, height=40)[0])
