import json
from pathlib import Path

import pytest

import pwcore as pc
import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
G = pp.graph(json.loads((FIX / "pwdump_patch.json").read_text()))
SRCS = [s for s in G.sources if not s.monitor]


def si(label):
    return next(i for i, s in enumerate(SRCS) if s.label == label)


def di(label):
    return next(i for i, d in enumerate(G.dests) if d.label == label)


def key(st, *keys, g=G):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, [], patch=g)
        actions += a
    return st, actions


def at(src, dst=None, **kw):
    return pc.UIState(tab="patch", patch_src=si(src), patch_dst=di(dst) if dst else 0, **kw)


def screen(st, width=100, height=40, g=G):
    return pc.render_screen(st, CUR, None, [], width, height, patch=g)


def body(lines):
    return "\n".join(t for t, _ in lines)


def hit_for(hits, target):
    for row, c0, c1, t in hits:
        if t == target:
            return row, c0, c1
    raise AssertionError(f"no hit for {target}: {[h[3] for h in hits][:20]}")


# --- tab -------------------------------------------------------------------------------

def test_patch_is_fifth_tab():
    st, _ = key(pc.UIState(), "5")
    assert st.tab == "patch"
    st, _ = key(pc.UIState(tab="devices"), "tab")
    assert st.tab == "patch"
    st, _ = key(st, "tab")
    assert st.tab == "settings"
    assert "5 Patch" in screen(pc.UIState())[0][0][0]


def test_loading_when_graph_missing():
    lines, _ = pc.render_screen(pc.UIState(tab="patch"), CUR, None, [], 80, 20, patch=None)
    assert "loading" in body(lines)
    for k in ("enter", "g", "p", "down", "right"):
        assert pc.handle_key(pc.UIState(tab="patch"), k, CUR, [], patch=None)[1] == []


# --- column view -------------------------------------------------------------------------

def test_columns_mark_targets_of_selected_source():
    text = body(screen(at("Discord"))[0])
    assert "Sources" in text and "Destinations" in text
    row = next(line for line in text.splitlines() if "GCS-8 CH 4-AUX" in line and "│" in line)
    assert "●" in row.split("│")[1]
    other = next(line for line in text.splitlines() if "GCS-8 CH 3-AUX" in line and "│" in line)
    assert "●" not in other.split("│")[1]
    assert "Discord → GCS-8 CH 4-AUX" in text  # the status line for the selected pair


def test_monitors_hidden_until_m():
    assert "(monitor)" not in body(screen(pc.UIState(tab="patch"))[0])
    st, _ = key(pc.UIState(tab="patch"), "m")
    assert st.patch_monitors and "(monitor)" in body(screen(st, height=60)[0])


def test_column_keys_move_and_switch_sides():
    st, _ = key(pc.UIState(tab="patch"), "down")
    assert st.patch_src == 1 and st.patch_side == "src"
    st, _ = key(st, "right", "down", "down")
    assert st.patch_side == "dst" and st.patch_dst == 2
    st, _ = key(st, "left")
    assert st.patch_side == "src"


def test_enter_on_source_moves_to_destinations():
    st, actions = key(at("GCS-8 CH 1"), "enter")
    assert actions == [] and st.patch_side == "dst"


def test_enter_on_destination_toggles():
    st = at("GCS-8 CH 1", "GCS-8 CH 3-AUX", patch_side="dst")
    _, actions = key(st, "enter")
    s, d = SRCS[si("GCS-8 CH 1")], G.dests[di("GCS-8 CH 3-AUX")]
    assert actions == [("patch_cmds", pp.connect_cmds(G, s, d))]
    _, actions = key(at("Discord", "GCS-8 CH 4-AUX", patch_side="dst"), "enter")
    assert actions[0][0] == "patch_cmds" and all(c[:2] == ["pw-link", "-d"] for c in actions[0][1])


# --- grid view ---------------------------------------------------------------------------

def test_g_switches_to_grid_and_back():
    st, _ = key(pc.UIState(tab="patch"), "g")
    assert st.patch_view == "grid"
    st, _ = key(st, "g")
    assert st.patch_view == "cols"


def test_grid_shows_dots_and_connections():
    st = at("Discord", "GCS-8 CH 4-AUX", patch_view="grid")
    text = body(screen(st)[0])
    discord = next(line for line in text.splitlines() if line.lstrip("> ").startswith("Discord") and "·" in line)
    assert "●" in discord
    assert "Discord → GCS-8 CH 4-AUX" in text
    assert f"{di('GCS-8 CH 4-AUX') + 1} GCS-8 CH 4-AUX" in text  # the numbered key


def test_grid_arrows_move_the_cell_and_enter_toggles():
    st, _ = key(at("GCS-8 CH 1", patch_view="grid"), "right", "right", "down")
    assert st.patch_dst == 2 and st.patch_src == si("GCS-8 CH 1") + 1
    st = at("GCS-8 CH 1", "GCS-8 CH 3-AUX", patch_view="grid")
    _, actions = key(st, "enter")
    assert actions and actions[0][0] == "patch_cmds" and actions[0][1][0][1] != "-d"


def test_grid_cursor_cell_is_bracketed():
    text = body(screen(at("Discord", "GCS-8 CH 4-AUX", patch_view="grid"))[0])
    assert "[●]" in text


# --- ports -------------------------------------------------------------------------------

def test_p_opens_ports_for_the_pair_and_toggles_one_link():
    st, _ = key(at("GCS-8 CH 1", "GCS-8 CH 3-AUX", patch_side="dst"), "p")
    assert st.patch_ports
    text = body(screen(st)[0])
    assert "capture_FL" in text and "playback_FR" in text
    st, _ = key(st, "down")
    _, actions = key(st, "enter")
    s, d = SRCS[si("GCS-8 CH 1")], G.dests[di("GCS-8 CH 3-AUX")]
    a, b, _ = pp.port_pairs(G, s, d)[1]
    assert actions == [("patch_cmds", [pp.port_toggle_cmd(G, a, b)])]
    st, _ = key(st, "esc")
    assert not st.patch_ports


def test_ports_mode_works_from_grid_too():
    st, _ = key(at("Discord", "GCS-8 CH 4-AUX", patch_view="grid"), "p")
    assert st.patch_ports and "●" in body(screen(st)[0])


# --- legend and width ------------------------------------------------------------------------

@pytest.mark.parametrize("view", ["cols", "grid"])
def test_legend_fits_80(view):
    lines, _ = screen(pc.UIState(tab="patch", patch_view=view), width=80)
    text = body(lines)
    for k in ("Enter connect/disconnect", "p ports", "m monitors", "q quit", "g "):
        assert k in text, k
    assert all(len(t.rstrip()) <= 80 for t, _ in lines)


def test_scrolls_to_keep_cursor_visible():
    st = pc.UIState(tab="patch", patch_src=len(SRCS) - 1)
    lines, _ = screen(st, height=14)
    assert any(SRCS[-1].label in t and t.startswith(">") for t, _ in lines)


# --- mouse -------------------------------------------------------------------------------------

def click(st, target, event="click"):
    lines, hits = screen(st)
    row, c0, _ = hit_for(hits, target)
    return pc.handle_mouse(st, hits, row, c0, event, CUR, [], patch=G)


def test_click_rows_and_double_click_toggles():
    st, _ = click(pc.UIState(tab="patch"), ("patch_src", si("GCS-8 CH 1")))
    assert st.patch_src == si("GCS-8 CH 1") and st.patch_side == "src"
    st2, _ = click(st, ("patch_dst", di("GCS-8 CH 3-AUX")))
    assert st2.patch_dst == di("GCS-8 CH 3-AUX") and st2.patch_side == "dst"
    _, actions = click(st, ("patch_dst", di("GCS-8 CH 3-AUX")), "double")
    assert actions and actions[0][0] == "patch_cmds"


def test_click_grid_cell():
    st = pc.UIState(tab="patch", patch_view="grid")
    st, _ = click(st, ("patch_cell", si("Discord"), di("GCS-8 CH 4-AUX")))
    assert (st.patch_src, st.patch_dst) == (si("Discord"), di("GCS-8 CH 4-AUX"))
