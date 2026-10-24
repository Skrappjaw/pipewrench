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
    st = pc.UIState()
    lines, _ = screen(st)
    body = "\n".join(t for t, _ in lines)
    assert "Force sample rate" in body and "48000" in body
    assert "Force buffer size" in body and "128" in body
    assert "*" not in body
    st, _ = key(st, "down", "down", "down", "right")  # force-quantum 128 -> 256 pending
    lines, _ = screen(st)
    row = [t for t, _ in lines if "Force buffer size" in t][0]
    assert "256" in row and "*" in row


def test_settings_render_shows_allowed_rates_and_warning():
    st, _ = key(pc.UIState(), "right")  # force-rate 48000 -> 88200 pending
    lines, _ = screen(st)
    body = "\n".join(t for t, _ in lines)
    assert "allowed rates: 48000" in body
    assert "not in allowed" in body


@pytest.mark.parametrize("width", [40, 80, 120])
def test_lines_fit_width(width):
    for tab in ("settings", "top", "presets"):
        lines, hits = screen(pc.UIState(tab=tab), width=width)
        assert all(len(t) <= width for t, _ in lines)
        assert all(c1 <= width for _, _, c1, _ in hits)


def test_lines_fit_height():
    lines, _ = screen(pc.UIState(tab="top"), height=10)
    assert len(lines) <= 10


def test_top_render_styles():
    lines, _ = pc.render_screen(pc.UIState(tab="top"), CUR, block(), PRESETS, 200, 40)
    styles = {t.split()[-1] if t.split() else "": s for t, s in lines}
    assert any("QUANT" in t for t, _ in lines)
    assert styles["Dummy-Driver"] == "dim"
    assert styles["alsa_input.usb-Hercules_Rocksmith_USB_Guitar_Adapter-00.mono-fallback"] == "normal"
    assert styles["SYSTEM"] == "alert"  # the real capture has ERR=19 on SYSTEM
    hdr = (FIX / "pwtop_batch.txt").read_text().splitlines()[0]
    p = pc.TopParser()
    p.feed(hdr)
    p.feed("R  155    227  44100  24.6us  32.9us  0.01  0.01    2    S16LE 2 44100  + ALSA plug-in [sunvox]")
    lines, _ = pc.render_screen(pc.UIState(tab="top"), CUR, p.flush(), PRESETS, 120, 40)
    assert [s for t, s in lines if "sunvox" in t] == ["alert"]


def test_top_render_waiting_for_data():
    lines, _ = pc.render_screen(pc.UIState(tab="top"), CUR, None, PRESETS, 80, 20)
    assert any("waiting for pw-top" in t for t, _ in lines)


def test_presets_render_lists_labels():
    lines, _ = screen(pc.UIState(tab="presets"))
    body = "\n".join(t for t, _ in lines)
    assert "tracking" in body and "Low-latency tracking" in body and "buf 64" in body


def test_presets_render_empty_hint():
    lines, _ = pc.render_screen(pc.UIState(tab="presets"), CUR, None, [], 80, 20)
    assert any("no presets" in t for t, _ in lines)


# --- 7. keys -----------------------------------------------------------------

def test_up_down_moves_cursor_and_clamps():
    st, _ = key(pc.UIState(), "up")
    assert st.cursor == 0
    st, _ = key(st, *["down"] * 10)
    assert st.cursor == len(pc.SETTINGS) - 1


def test_left_right_sets_pending_without_action():
    st, actions = key(pc.UIState(), "down", "down", "down", "right", "right")
    assert st.pending == {"clock.force-quantum": 512}
    assert actions == []


def test_pending_back_to_current_clears():
    st, _ = key(pc.UIState(), "down", "down", "down", "right", "left")
    assert st.pending == {}


def test_enter_applies_all_pending_in_order():
    st, actions = key(pc.UIState(), "down", "down", "down", "right", "up", "up", "right", "enter")
    # cursor 3 (force buffer) -> up, up -> cursor 1 (min buffer) 32 -> 64
    assert actions == [("set_many", [("clock.min-quantum", 64), ("clock.force-quantum", 256)])]
    assert st.pending == {}


def test_enter_with_nothing_pending_does_nothing():
    _, actions = key(pc.UIState(), "enter")
    assert actions == []


def test_enter_with_invalid_pending_blocks_and_reports():
    # min-quantum up to 4096 (> max 2048)
    st, actions = key(pc.UIState(), "down", *["right"] * 7, "enter")
    assert st.pending == {"clock.min-quantum": 4096}
    assert actions == []
    assert "min" in st.message


def test_esc_reverts():
    st, _ = key(pc.UIState(), "right", "esc")
    assert st.pending == {}


def test_tab_and_number_keys_switch_tabs():
    st, _ = key(pc.UIState(), "tab")
    assert st.tab == "top"
    st, _ = key(st, "tab")
    assert st.tab == "presets"
    st, _ = key(st, "tab")
    assert st.tab == "devices"
    st, _ = key(st, "tab")
    assert st.tab == "settings"
    for k, tab in (("2", "top"), ("3", "presets"), ("4", "devices"), ("1", "settings")):
        st, _ = key(st, k)
        assert st.tab == tab


def test_restart_needs_confirmation():
    st, actions = key(pc.UIState(), "R")
    assert actions == [] and st.confirm == ("restart",)
    st2, actions = key(st, "n")
    assert actions == [] and st2.confirm is None
    st3, actions = key(st, "y")
    assert actions == [("restart_wireplumber",)] and st3.confirm is None


def test_confirm_swallows_other_keys():
    st, _ = key(pc.UIState(), "R")
    st, actions = key(st, "q")
    assert actions == [] and st.confirm == ("restart",)


def test_q_quits():
    assert key(pc.UIState(), "q")[1] == [("quit",)]


def test_presets_keys():
    st = pc.UIState(tab="presets")
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
    st = pc.UIState(tab="presets")
    _, actions = key(st, "enter", "d", "e", presets=[])
    assert actions == []
    _, actions = key(st, "s", presets=[])
    assert actions == [("prompt_save",)]


def test_top_scroll_keys():
    st, _ = key(pc.UIState(tab="top"), "down", "down")
    assert st.top_scroll == 2
    st, _ = key(st, "up", "up", "up")
    assert st.top_scroll == 0


# --- 9. mouse ----------------------------------------------------------------

def click(st, hits, target, event="click", offset=0):
    row, c0, _ = hit_for(hits, target)
    return pc.handle_mouse(st, hits, row, c0 + offset, event, CUR, PRESETS)


@pytest.mark.parametrize("width", [80, 120])
def test_hitmap_lands_on_drawn_glyphs(width):
    lines, hits = screen(pc.UIState(), width=width)
    for i in range(len(pc.SETTINGS)):
        r, c0, _ = hit_for(hits, ("arrow", i, -1))
        assert text_at(lines, r, c0) == "◀"
        r, c0, _ = hit_for(hits, ("arrow", i, +1))
        assert text_at(lines, r, c0) == "▶"
    r, c0, c1 = hit_for(hits, ("button", "apply"))
    assert lines[r][0][c0:c1] == "[Apply]"
    r, c0, c1 = hit_for(hits, ("tab", "top"))
    assert "Top" in lines[r][0][c0:c1]


def test_click_tab_switches():
    st = pc.UIState()
    _, hits = screen(st)
    st, actions = click(st, hits, ("tab", "presets"))
    assert st.tab == "presets" and actions == []


def test_click_row_selects():
    st = pc.UIState()
    _, hits = screen(st)
    st, _ = click(st, hits, ("setting", 3))
    assert st.cursor == 3 and st.pending == {}


def test_click_arrow_equals_keys():
    st = pc.UIState()
    _, hits = screen(st)
    via_mouse, a1 = click(st, hits, ("arrow", 3, +1))
    via_keys, a2 = key(st, "down", "down", "down", "right")
    assert via_mouse.pending == via_keys.pending and via_mouse.cursor == via_keys.cursor
    assert a1 == a2 == []


def test_wheel_over_row_steps_value():
    st = pc.UIState()
    _, hits = screen(st)
    st, _ = click(st, hits, ("setting", 3), event="wheel_up")
    assert st.pending == {"clock.force-quantum": 256}
    st, _ = click(st, hits, ("setting", 3), event="wheel_down")
    assert st.pending == {}


def test_apply_and_revert_buttons():
    st, _ = key(pc.UIState(), "down", "down", "down", "right")
    _, hits = screen(st)
    _, actions = click(st, hits, ("button", "apply"))
    assert actions == key(st, "enter")[1]
    st2, actions = click(st, hits, ("button", "revert"))
    assert st2.pending == {} and actions == []


def test_double_click_row_applies():
    st, _ = key(pc.UIState(), "down", "down", "down", "right")
    _, hits = screen(st)
    _, actions = click(st, hits, ("setting", 3), event="double")
    assert actions == [("set_many", [("clock.force-quantum", 256)])]


def test_restart_button_needs_confirmation():
    st = pc.UIState()
    _, hits = screen(st)
    st, actions = click(st, hits, ("button", "restart"))
    assert actions == [] and st.confirm == ("restart",)


def test_click_outside_targets_does_nothing():
    st = pc.UIState(cursor=2)
    _, hits = screen(st)
    st2, actions = pc.handle_mouse(st, hits, 29, 79, "click", CUR, PRESETS)
    assert st2 == st and actions == []


def test_presets_mouse():
    st = pc.UIState(tab="presets")
    _, hits = screen(st)
    st, actions = click(st, hits, ("preset", 1))
    assert st.preset_cursor == 1 and actions == []
    _, actions = click(st, hits, ("preset", 0), event="double")
    assert actions == [("apply_preset", "tracking")]
    _, actions = click(st, hits, ("button", "preset_save"))
    assert actions == [("prompt_save",)]
    st2, actions = click(st, hits, ("button", "preset_delete"))
    assert actions == [] and st2.confirm == ("delete", "mix")


def test_wheel_scrolls_top():
    st = pc.UIState(tab="top")
    _, hits = screen(st)
    st, _ = click(st, hits, ("toplist",), event="wheel_down")
    assert st.top_scroll == 1
    st, _ = click(st, hits, ("toplist",), event="wheel_up")
    assert st.top_scroll == 0


def test_mouse_ignored_while_confirming():
    st, _ = key(pc.UIState(), "R")
    _, hits = screen(st)
    st2, actions = click(st, hits, ("tab", "top"))
    assert st2.tab == "settings" and actions == []
