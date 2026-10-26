"""Popovers: warnings and questions in a box over the screen, answered with ←→ and Enter (Skrappjaw, 2026-10-26:
"no shortcut keys. use arrow nav + enter to select"). They stay until answered; Esc cancels."""
from pathlib import Path

import pytest

import pwcore as pc

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())


def key(st, *keys):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR)
        actions += a
    return st, actions


def screen(st, width=110, height=40):
    return pc.render_screen(st, CUR, None, width, height)


def restart():
    return pc.UIState(tab="about", popup=("restart",))


def test_a_confirm_has_its_action_and_cancel_with_cancel_selected():
    title, text, buttons = pc.popup_view(restart())
    assert "WirePlumber" in text and [b for b, _ in buttons] == ["Restart", "Cancel"]
    assert pc.popup_cursor(restart()) == 1


def test_arrows_move_and_enter_picks():
    st, actions = key(restart(), "left")
    assert st.popup == ("restart",) and actions == [] and pc.popup_cursor(st) == 0
    st, actions = key(st, "enter")
    assert actions == [("restart_wireplumber",)] and st.popup is None


def test_enter_on_cancel_and_esc_cancel():
    for keys in (("enter",), ("esc",), ("left", "right", "enter")):
        st, actions = key(restart(), *keys)
        assert actions == [] and st.popup is None and st.message == "cancelled"


@pytest.mark.parametrize("k", ["y", "n", "q", "up", "down", " ", "tab", "1"])
def test_every_other_key_leaves_it_up(k):
    st, actions = key(restart(), k)
    assert st.popup == ("restart",) and actions == []


def test_arrows_stop_at_the_ends():
    st, _ = key(restart(), "right", "right")
    assert pc.popup_cursor(st) == 1
    st, _ = key(st, "left", "left", "left")
    assert pc.popup_cursor(st) == 0


def test_reset_keys_runs_in_place():
    st = pc.UIState(tab="settings", keys={"mute": "x"}, popup=("reset_keys",))
    st, actions = key(st, "left", "enter")
    assert st.keys == {} and actions == [("save_ui",)]


def test_pw_down_has_only_ok():
    st = pc.UIState(popup=("pw_down",))
    title, text, buttons = pc.popup_view(st)
    assert "PipeWire" in text and [b for b, _ in buttons] == ["OK"] and pc.popup_cursor(st) == 0
    st, actions = key(st, "enter")
    assert st.popup is None and actions == [] and st.message != "cancelled"


@pytest.mark.parametrize("width", [80, 110])
def test_the_box_is_drawn_over_the_screen_with_its_buttons(width):
    lines, hits = screen(restart(), width=width)
    assert all(len(t) <= width for t, _ in lines)
    body = [t for t, _ in lines]
    top = next(n for n, t in enumerate(body) if "┌─ Restart WirePlumber" in t)
    assert any("└" in t for t in body[top:]) and any("[ Restart ]" in t and "[ Cancel ]" in t for t in body[top:])
    btn_row = next(n for n, t in enumerate(body) if "[ Restart ]" in t)
    roles = [r for _, _, r in lines[btn_row][1][1]]
    assert roles[-2:] == ["chip", "selected"]           # Cancel, the default, is the selected button
    assert {h[3] for h in hits} == {("popup_btn", 0), ("popup_btn", 1)}  # only the buttons take clicks


def test_the_keys_box_lists_the_popover_keys():
    assert [k for k, _ in pc.legend(restart())] == ["←→", "Enter", "Esc"]


def test_a_click_on_a_button_picks_it_and_elsewhere_does_nothing():
    st = restart()
    lines, hits = screen(st)
    row, c0, c1, _ = next(h for h in hits if h[3] == ("popup_btn", 0))
    st2, actions = pc.handle_mouse(st, hits, row, c0 + 1, "click", CUR)
    assert actions == [("restart_wireplumber",)] and st2.popup is None
    st3, actions = pc.handle_mouse(st, hits, 0, 0, "click", CUR)
    assert st3.popup == ("restart",) and actions == []


def test_long_text_wraps_inside_the_box():
    st = pc.UIState(tab="about", popup=("restart_labels",))
    lines, _ = screen(st, width=80)
    inside = [t for t, _ in lines if "│" in t and "Keys" not in t]
    assert len(inside) >= 4 and all(len(t) <= 80 for t, _ in lines)


def test_a_narrow_screen_stacks_the_buttons():
    st = pc.UIState(tab="about", popup=("vol_boost", 1, "n", "Discord", ("wpctl",)))
    lines, hits = screen(st, width=40)
    assert all(len(t) <= 40 for t, _ in lines)
    rows = {h[0] for h in hits}
    assert len(rows) == 3 and {h[3] for h in hits} == {("popup_btn", 0), ("popup_btn", 1), ("popup_btn", 2)}
