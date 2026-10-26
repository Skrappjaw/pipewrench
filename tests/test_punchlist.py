"""Skrappjaw's punch list, 2026-10-25 (punch-list.md): Enter before ←→ edits, ← goes back, Shift+Tab,
latency in ms, and leaving a view resets it."""
import curses
from pathlib import Path
from types import SimpleNamespace

import pwcore as pc
import pipewrench

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())  # force rate 48000, force buffer 128
RATE, FORCE_Q = 0, [s.key for s in pc.SETTINGS].index("clock.force-quantum")   # SETTINGS rows


def S(**kw):
    kw.setdefault("tab", "settings")
    kw.setdefault("settings_focus", "pane")
    return pc.UIState(**kw)


def key(st, *keys, cur=CUR):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, cur)
        actions += a
    return st, actions


def body(st, width=100, cur=CUR):
    lines, _ = pc.render_screen(st, cur, None, width, 50)
    return "\n".join(t for t, _ in lines)


# --- 1. Enter before ←→ changes a Settings value --------------------------------------------------

def test_arrows_do_not_change_a_clock_value_until_enter():
    st, actions = key(S(cursor=FORCE_Q), "right")
    assert st.pending == {} and actions == []


def test_enter_then_arrows_edit_and_enter_applies():
    st, _ = key(S(cursor=FORCE_Q), "enter", "right")
    assert st.editing and st.pending == {"clock.force-quantum": 256}
    st, actions = key(st, "enter")
    assert not st.editing and st.pending == {}
    assert actions == [("set_many", [("clock.force-quantum", 256)])]


def test_esc_while_editing_puts_the_row_back():
    st, actions = key(S(cursor=FORCE_Q), "enter", "right", "right", "esc")
    assert not st.editing and st.pending == {} and actions == []
    assert st.settings_focus == "pane"  # Esc cancels the edit; it doesn't leave the pane


def test_up_down_end_the_edit_and_keep_it_pending():
    st, _ = key(S(cursor=FORCE_Q), "enter", "right", "up")
    assert not st.editing and st.cursor == FORCE_Q - 1 and st.pending == {"clock.force-quantum": 256}


def test_preference_rows_need_enter_too_and_esc_restores():
    st, actions = key(S(settings_cat="prefs", cursor=pc.THEME_ROW), "right")
    assert st.theme == "dark" and actions == []
    st, actions = key(st, "enter", "right")
    assert st.editing and st.theme != "dark" and actions == [("set_theme", st.theme)]
    st, actions = key(st, "esc")
    assert not st.editing and st.theme == "dark" and actions == [("set_theme", "dark")]
    st, _ = key(st, "enter", "right", "enter")
    assert not st.editing and st.theme != "dark"


def test_meter_rows_edit_after_enter():
    st, _ = key(S(settings_cat="prefs", cursor=pc.FPS_ROW), "right")
    assert st.meter_fps == 10
    st, actions = key(st, "enter", "right", "enter")
    assert st.meter_fps > 10 and ("save_ui",) in actions


def test_a_click_on_an_arrow_edits_at_once():
    """Taps on ◀ ▶ are deliberate (the touch desk needs them): they start the edit themselves."""
    st = S(cursor=0)
    lines, hits = pc.render_screen(st, CUR, None, 100, 50)
    row, c0, _ = next((r, a, b) for r, a, b, t in hits if t == ("arrow", FORCE_Q, +1))
    st, _ = pc.handle_mouse(st, hits, row, c0, "click", CUR)
    assert st.editing and st.cursor == FORCE_Q and st.pending == {"clock.force-quantum": 256}


def test_the_edited_row_says_how_to_finish():
    st, _ = key(S(cursor=FORCE_Q), "enter")
    assert ("Esc", "cancel") in pc.legend(st)
    assert ("Enter", "edit") in pc.legend(S(cursor=FORCE_Q))


# --- 2. ← goes back to the Settings menu -----------------------------------------------------------

def test_left_in_the_pane_goes_back_to_the_menu():
    st, _ = key(S(cursor=RATE), "left")
    assert st.settings_focus == "menu"


def test_left_on_the_buttons_row_picks_buttons_then_goes_back_from_apply():
    st, _ = key(S(cursor=pc.CLOCK_BUTTONS_ROW, clock_button=2), "left")
    assert st.settings_focus == "pane" and st.clock_button == 1
    st, _ = key(st, "left", "left")
    assert st.settings_focus == "menu"


def test_left_on_preferences_and_keys_rows_goes_back():
    for row in (pc.THEME_ROW, pc.KEYS_ROW, pc.RESET_KEYS_ROW):
        st, _ = key(S(settings_cat="prefs", cursor=row), "left")
        assert st.settings_focus == "menu", row


def test_right_still_opens_the_pane_from_the_menu():
    st, _ = key(S(settings_focus="menu"), "right")
    assert st.settings_focus == "pane"


# --- 3. Shift+Tab -----------------------------------------------------------------------------------

def test_shift_tab_goes_back_a_tab_and_wraps():
    st, _ = key(pc.UIState(tab="devices"), "btab")
    assert st.tab == "mixer"
    st, _ = key(st, "btab")
    assert st.tab == pc.TABS[-1]
    assert key(st, "tab")[0].tab == "mixer"


def test_the_terminal_shift_tab_code_is_btab():
    tui = SimpleNamespace(curses=curses)
    assert pipewrench.Tui._key(tui, curses.KEY_BTAB) == "btab"


# --- 4. latency in ms ---------------------------------------------------------------------------------

def test_latency_shows_beside_the_rate_and_buffer_rows():
    rows = {line.split("◀")[0].split("│")[-1].strip(" >"): line for line in body(S()).splitlines() if "◀" in line}
    assert "2.67 ms" in rows["Force buffer size"] and "2.67 ms" in rows["Force sample rate"]  # 128 at 48000
    assert "0.67 ms" in rows["Minimum buffer"] and "42.7 ms" in rows["Max buffer"]


def test_latency_follows_pending_values():
    st, _ = key(S(cursor=RATE), "enter", *["right"] * 4)  # 48000 → 192000
    assert st.pending == {"clock.force-rate": 192000}
    row = next(line for line in body(st).splitlines() if "Force buffer size" in line)
    assert "0.67 ms" in row  # 128 at 192k


def test_latency_without_forcing_uses_the_default_rate_and_buffer():
    cur = {**CUR, "clock.force-rate": 0, "clock.force-quantum": 0}
    assert "21.3 ms" in body(S(), cur=cur)  # 1024 at 48000


def test_latency_format():
    assert pc.latency_ms(128, 48000) == "2.67 ms" and pc.latency_ms(1024, 48000) == "21.3 ms"
    assert pc.latency_ms(None, 48000) == "" and pc.latency_ms(128, 0) == ""


# --- 5. leaving a view resets it -------------------------------------------------------------------

def test_tab_away_drops_pending_and_editing():
    st, _ = key(S(cursor=FORCE_Q), "enter", "right")
    for k in ("tab", "btab", "1"):
        left, _ = key(st, k)
        assert left.pending == {} and not left.editing and left.settings_focus == "menu", k
        assert "dropped" in left.message, k
        back, _ = key(left, "4")
        assert back.tab == "settings" and back.settings_focus == "menu" and back.pending == {}


def test_tab_away_closes_open_menus():
    st = pc.UIState(tab="mixer", mix_menu=("playback", 1))
    assert key(st, "tab")[0].mix_menu is None
    st = pc.UIState(tab="devices", dev_menu=("card", 95))
    assert key(st, "tab")[0].dev_menu is None
    st = pc.UIState(tab="patch", patch_menu=True, patch_ports=True)
    left = key(st, "tab")[0]
    assert not left.patch_menu and not left.patch_ports


def test_tab_away_keeps_list_positions_and_views():
    st = pc.UIState(tab="mixer", mix_cursor=4, dev_cursor=3, patch_src=2, patch_view="grid")
    left = key(st, "tab", "tab", "tab")[0]
    assert (left.mix_cursor, left.dev_cursor, left.patch_src, left.patch_view) == (4, 3, 2, "grid")


def test_tab_away_mid_preference_edit_restores_it():
    st, _ = key(S(settings_cat="prefs", cursor=pc.THEME_ROW), "enter", "right")
    left, actions = key(st, "tab")
    assert left.theme == "dark" and ("set_theme", "dark") in actions


def test_back_to_the_menu_drops_pending():
    st, _ = key(S(cursor=FORCE_Q), "enter", "right", "up")  # pending, not editing
    st, _ = key(st, "left")
    assert st.settings_focus == "menu" and st.pending == {} and "dropped" in st.message


def test_clicking_a_tab_or_category_resets_too():
    st, _ = key(S(cursor=FORCE_Q), "enter", "right")
    lines, hits = pc.render_screen(st, CUR, None, 100, 50)
    row, c0, _ = next((r, a, b) for r, a, b, t in hits if t == ("tab", "mixer"))
    left, _ = pc.handle_mouse(st, hits, row, c0, "click", CUR)
    assert left.tab == "mixer" and left.pending == {} and not left.editing
    row, c0, _ = next((r, a, b) for r, a, b, t in hits if t == ("settings_cat", "prefs"))
    left, _ = pc.handle_mouse(st, hits, row, c0, "click", CUR)
    assert left.settings_cat == "prefs" and left.pending == {} and not left.editing


def test_tapping_a_clock_button_while_editing_ends_the_edit():
    """Found by the QA sweep: an open row plus a tap on Save crashed (IndexError)."""
    st, _ = key(S(cursor=FORCE_Q), "enter")
    lines, hits = pc.render_screen(st, CUR, None, 100, 50)
    row, c0, _ = next((r, a, b) for r, a, b, t in hits if t == ("clock_button", 1))
    st, actions = pc.handle_mouse(st, hits, row, c0, "click", CUR)
    assert not st.editing and actions == [("save_clock",)]
