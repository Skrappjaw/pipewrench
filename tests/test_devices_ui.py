from pathlib import Path

import pytest

import pwcore as pc
import pwdevices as pd

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
M = pd.default_model()
ITEMS = pd.dev_items(M)


def view(m=M, dirty=False, missing=()):
    names = [pd.pair_node_name(p) for p in m["pairs"]]
    return {"model": m, "status": {n: n not in missing for n in names},
            "buses": {b["name"] for b in m["buses"]} - set(missing), "dirty": dirty}


def key(st, *keys, devices=None):
    devices = view() if devices is None else devices
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, [], devices=devices)
        actions += a
    return st, actions


def at(item):
    return pc.UIState(tab="devices", dev_cursor=ITEMS.index(item))


def screen(st, devices=None, width=100, height=34):
    return pc.render_screen(st, CUR, None, [], width, height, devices=view() if devices is None else devices)


def body(lines):
    return "\n".join(t for t, _ in lines)


def hit_for(hits, target):
    for row, c0, c1, t in hits:
        if t == target:
            return row, c0, c1
    raise AssertionError(f"no hit for {target}: {[h[3] for h in hits]}")


# --- tabs --------------------------------------------------------------------

def test_devices_is_fourth_tab():
    st, _ = key(pc.UIState(), "4")
    assert st.tab == "devices"
    st, _ = key(pc.UIState(tab="presets"), "tab")
    assert st.tab == "devices"
    st, _ = key(st, "tab")
    assert st.tab == "settings"
    assert "4 Devices" in screen(pc.UIState())[0][0][0]


# --- render ------------------------------------------------------------------

def test_render_groups_pairs_and_buses_with_status():
    text = body(screen(pc.UIState(tab="devices"), devices=view(missing={"pwctl.gcs8.in.ch2"}))[0])
    assert text.index("Inputs") < text.index("GCS-8 CH 2 (Mic)") < text.index("Outputs") < text.index("Buses")
    mic = next(line for line in text.splitlines() if "GCS-8 CH 2 (Mic)" in line)
    assert "missing" in mic and "AUX6/7" in mic
    music = next(line for line in text.splitlines() if "GCS-8 CH 3-AUX (Music)" in line)
    assert "ok" in music and "AUX8/9" in music


def test_render_marks_cursor_and_off_pairs():
    m = pd.toggle_pair(M, "in", "main")
    lines, _ = screen(at(("pair", "out", "ch3aux")), devices=view(m))
    sel = [t for t, style in lines if style == "selected"]
    assert len(sel) == 1 and "GCS-8 CH 3-AUX (Music)" in sel[0] and sel[0].startswith("> ")
    assert "off" in next(t for t, _ in lines if "GCS-8 MAIN " in t)


def test_render_dirty_note_only_when_dirty():
    assert "not applied" not in body(screen(pc.UIState(tab="devices"))[0])
    assert "not applied" in body(screen(pc.UIState(tab="devices"), devices=view(dirty=True))[0])


def test_render_without_devices_loaded():
    lines, _ = pc.render_screen(pc.UIState(tab="devices"), CUR, None, [], 80, 20, devices=None)
    assert "loading" in body(lines)


def test_render_unknown_status():
    v = {**view(), "status": None, "buses": None}
    assert " ? " in body(screen(pc.UIState(tab="devices"), devices=v)[0])


@pytest.mark.parametrize("width", [60, 100])
def test_lines_fit_width(width):
    lines, _ = screen(pc.UIState(tab="devices"), width=width)
    assert all(len(t) <= width for t, _ in lines)


def test_scrolls_to_keep_cursor_visible():
    st = at(("bus", "GAME"))
    lines, _ = screen(st, height=16)
    assert len(lines) == 16
    assert any(style == "selected" and "GAME" in t for t, style in lines)


# --- keys --------------------------------------------------------------------

def test_cursor_moves_and_clamps():
    st, _ = key(pc.UIState(tab="devices"), "up")
    assert st.dev_cursor == 0
    st, _ = key(st, *["down"] * 40)
    assert st.dev_cursor == len(ITEMS) - 1


def test_enter_prompts_for_label():
    _, actions = key(at(("pair", "in", "ch2")), "enter")
    assert actions == [("dev_prompt_label", ("pair", "in", "ch2"))]
    _, actions = key(at(("bus", "SYSTEM")), "enter")
    assert actions == [("dev_prompt_label", ("bus", "SYSTEM"))]


def test_space_toggles_pairs_only():
    _, actions = key(at(("pair", "in", "main")), " ")
    assert actions == [("dev_toggle", "in", "main")]
    st, actions = key(at(("bus", "SYSTEM")), " ")
    assert actions == [] and st.message


def test_add_bus_prompts():
    assert key(pc.UIState(tab="devices"), "a")[1] == [("dev_prompt_bus",)]


def test_delete_bus_needs_confirmation():
    st, actions = key(at(("bus", "GAME")), "d")
    assert actions == [] and st.confirm == ("dev_delete_bus", "GAME")
    assert "Delete bus 'GAME'" in body(screen(st)[0])
    st2, actions = key(st, "y")
    assert actions == [("dev_delete_bus", "GAME")] and st2.confirm is None
    st3, actions = key(st, "n")
    assert actions == [] and st3.confirm is None


def test_delete_on_pair_explains():
    st, actions = key(at(("pair", "in", "ch2")), "d")
    assert actions == [] and st.confirm is None and "space" in st.message


def test_preview_key():
    assert key(pc.UIState(tab="devices"), "p")[1] == [("dev_preview",)]


def test_apply_needs_confirmation_and_warns_about_restart():
    st, actions = key(pc.UIState(tab="devices"), "A")
    assert actions == [] and st.confirm == ("dev_apply",)
    assert "restart PipeWire" in body(screen(st)[0])
    assert key(st, "y")[1] == [("dev_apply",)]


def test_test_key_on_output_pair_only():
    assert key(at(("pair", "out", "ch3aux")), "t")[1] == [("dev_test", "ch3aux")]
    st, actions = key(at(("pair", "in", "ch2")), "t")
    assert actions == [] and "output" in st.message


def test_keys_do_nothing_until_devices_loaded():
    st = pc.UIState(tab="devices")
    for k in ("enter", " ", "d", "A", "t", "p", "a", "down"):
        _, actions = pc.handle_key(st, k, CUR, [], devices=None)
        assert actions == []


def test_other_tabs_unaffected_by_devices_arg():
    assert pc.handle_key(pc.UIState(), "R", CUR, [], devices=view())[0].confirm == ("restart",)


# --- mouse -------------------------------------------------------------------

def click(st, target, event="click"):
    lines, hits = screen(st)
    row, c0, _ = hit_for(hits, target)
    return pc.handle_mouse(st, hits, row, c0, event, CUR, [], devices=view())


def test_click_row_selects_and_double_click_edits():
    idx = ITEMS.index(("pair", "out", "ch3aux"))
    st, actions = click(pc.UIState(tab="devices"), ("device", idx))
    assert st.dev_cursor == idx and actions == []
    st, actions = click(pc.UIState(tab="devices"), ("device", idx), "double")
    assert actions == [("dev_prompt_label", ("pair", "out", "ch3aux"))]


def test_buttons_land_on_labels_and_map_to_keys():
    lines, hits = screen(pc.UIState(tab="devices"))
    for target, label in (("dev_apply", "[Apply]"), ("dev_preview", "[Preview]"),
                          ("dev_add_bus", "[Add bus]"), ("dev_test", "[Test]")):
        r, c0, c1 = hit_for(hits, ("button", target))
        assert lines[r][0][c0:c1] == label
    st, actions = click(pc.UIState(tab="devices"), ("button", "dev_apply"))
    assert st.confirm == ("dev_apply",) and actions == []
    _, actions = click(pc.UIState(tab="devices"), ("button", "dev_preview"))
    assert actions == [("dev_preview",)]


def test_wheel_moves_cursor():
    st, _ = click(pc.UIState(tab="devices"), ("device", 0), "wheel_down")
    assert st.dev_cursor == 1
