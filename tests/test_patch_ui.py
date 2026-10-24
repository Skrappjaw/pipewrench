import json
import re
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


def test_highlight_covers_only_the_active_column():
    def spans(st):
        return [style[1] for _, style in screen(st)[0] if isinstance(style, tuple) and style[0] == "selected"]
    (src,), = spans(at("Discord"))
    assert src[0] == 0 and src[1] < 60  # left column only
    (dst,), = spans(at("Discord", "GCS-8 CH 4-AUX", patch_side="dst"))
    assert dst[0] > 40 and dst[1] == 100  # right column only


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

from test_patch import with_reaper  # noqa: E402

GR = pp.graph(with_reaper(json.loads((FIX / "pwdump_patch.json").read_text())))


def reaper_ports_state():
    srcs = [s for s in GR.sources if not s.monitor]
    i = next(n for n, s in enumerate(srcs) if s.label == "GCS-8 CH 2")
    j = next(n for n, d in enumerate(GR.dests) if d.label == "REAPER")
    st, _ = key(pc.UIState(tab="patch", patch_src=i, patch_dst=j, patch_side="dst"), "p", g=GR)
    return st, srcs[i], GR.dests[j]


def test_p_opens_a_port_matrix():
    st, s, d = reaper_ports_state()
    assert st.patch_ports and (st.patch_port_row, st.patch_port_col) == (0, 0)
    text = body(screen(st, g=GR)[0])
    header = next(line for line in text.splitlines() if "in1" in line and "in8" in line)
    assert header.index("in1") < header.index("in8")
    rows = [line for line in text.splitlines() if line.lstrip("> ").startswith(("FL", "FR")) and ("·" in line or "●" in line)]
    assert len(rows) == 2                      # one row per source port, not 16 lines
    assert "[·]" in rows[0]                    # cursor on FL × in1


def test_port_matrix_arrows_and_enter_toggle_one_link():
    st, s, d = reaper_ports_state()
    st, _ = key(st, "down", "right", "right", g=GR)
    assert (st.patch_port_row, st.patch_port_col) == (1, 2)
    _, actions = key(st, "enter", g=GR)
    assert actions == [("patch_cmds", [pp.port_toggle_cmd(GR, s.ports[1], d.ports[2])])]
    st, _ = key(st, "esc", g=GR)
    assert not st.patch_ports


def test_port_matrix_shows_existing_links():
    st, _ = key(at("Discord", "GCS-8 CH 4-AUX", patch_side="dst"), "p")
    text = body(screen(st)[0])
    rows = [line for line in text.splitlines() if line.lstrip("> ").startswith(("FL", "FR")) and ("·" in line or "●" in line)]
    assert "●" in rows[0] and "●" in rows[1]   # FL→FL and FR→FR linked
    assert "Ports: Discord → GCS-8 CH 4-AUX" in text


def test_ports_mode_works_from_grid_too():
    st, _ = key(at("Discord", "GCS-8 CH 4-AUX", patch_view="grid"), "p")
    assert st.patch_ports and "●" in body(screen(st)[0])


def test_port_matrix_scrolls_sideways_on_narrow_screens():
    st, _, _ = reaper_ports_state()
    st, _ = key(st, *["right"] * 7, g=GR)
    text = body(screen(st, width=40, g=GR)[0])
    assert "in8" in text and "[·]" in text


def test_click_port_cell():
    st, s, d = reaper_ports_state()
    lines, hits = screen(st, g=GR)
    row, c0, _ = hit_for(hits, ("patch_pcell", 1, 3))
    st2, _ = pc.handle_mouse(st, hits, row, c0, "click", CUR, [], patch=GR)
    assert (st2.patch_port_row, st2.patch_port_col) == (1, 3)
    _, actions = pc.handle_mouse(st, hits, row, c0, "double", CUR, [], patch=GR)
    assert actions == [("patch_cmds", [pp.port_toggle_cmd(GR, s.ports[1], d.ports[3])])]


# --- legend and width ------------------------------------------------------------------------

@pytest.mark.parametrize("view", ["cols", "grid"])
def test_legend_fits_80(view):
    lines, _ = screen(pc.UIState(tab="patch", patch_view=view), width=80)
    text = re.sub(r"[ │]+", " ", body(lines))  # chips pad keys with spaces
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


# --- journey: source, then destination, then port ----------------------------------------

def test_p_on_the_source_side_asks_for_a_destination_first():
    st, actions = key(at("Discord", "GCS-8 CH USB"), "p")
    assert not st.patch_ports and actions == []
    assert st.patch_side == "dst" and "destination" in st.message


def test_moving_to_destinations_lands_on_the_first_target():
    st, _ = key(at("Discord", "GCS-8 CH USB"), "right")
    assert st.patch_side == "dst" and st.patch_dst == di("GCS-8 CH 4-AUX")
    st, _ = key(at("Discord", "GCS-8 CH USB"), "enter")
    assert st.patch_dst == di("GCS-8 CH 4-AUX")


def test_unconnected_source_keeps_the_destination_cursor():
    st, _ = key(at("GCS-8 CH 1", "GCS-8 CH 3-AUX"), "right")
    assert st.patch_dst == di("GCS-8 CH 3-AUX")


def test_full_journey_opens_ports_for_the_chosen_pair():
    st, _ = key(at("GCS-8 CH 1", "Discord"), "enter", "down", "p")   # source, move to dests, pick, ports
    assert st.patch_ports
    assert f"Ports: GCS-8 CH 1 → {G.dests[di('Discord') + 1].label}" in body(screen(st)[0])


@pytest.mark.parametrize("width", [80, 120])
def test_grid_key_fits_the_screen(width):
    lines, _ = screen(pc.UIState(tab="patch", patch_view="grid"), width=width)
    key_rows = [t for t, _ in lines if t.strip()[:2].strip().isdigit() and "·" not in t]
    text = "\n".join(key_rows)
    for n, d in enumerate(G.dests):
        assert f"{n + 1} {d.label[:20]}" in text, (n, d.label, text)


# --- slot menu ---------------------------------------------------------------------------

def reaper_menu(src="GCS-8 CH 2"):
    srcs = [s for s in GR.sources if not s.monitor]
    i = next(n for n, s in enumerate(srcs) if s.label == src)
    j = next(n for n, d in enumerate(GR.dests) if d.label == "REAPER")
    return key(pc.UIState(tab="patch", patch_src=i, patch_dst=j, patch_side="dst"), "enter", g=GR)


def test_enter_on_unnamed_inputs_opens_the_slot_menu():
    st, actions = reaper_menu()
    assert st.patch_menu and actions == []
    lines, hits = screen(st, g=GR)
    text = body(lines)
    assert "In 1-2" in text and "In 7-8" in text
    assert pc.legend(st) == pc.LEGENDS["patch menu"]
    assert any(t[0] == "patch_slot" for *_, t in hits)


def test_slot_menu_enter_links_the_chosen_slot():
    st, _ = reaper_menu()
    st, actions = key(st, "down", "enter", g=GR)
    assert not st.patch_menu
    r = next(d for d in GR.dests if d.label == "REAPER")
    assert [c[2] for c in actions[0][1]] == [str(r.ports[2].id), str(r.ports[3].id)]


def test_slot_menu_closes_on_esc():
    st, _ = reaper_menu()
    st, actions = key(st, "esc", g=GR)
    assert not st.patch_menu and actions == []


def test_named_destination_still_toggles_at_once():
    st, actions = key(at("Discord", "GCS-8 CH 3-AUX", patch_side="dst"), "enter")
    assert not st.patch_menu and actions and actions[0][0] == "patch_cmds"


def test_clicking_a_slot_picks_it():
    st, _ = reaper_menu()
    _, hits = screen(st, g=GR)
    row, c0, _, t = next(h for h in hits if h[3] == ("patch_slot", 1))
    st, actions = pc.handle_mouse(st, hits, row, c0, "click", CUR, [], patch=GR)
    assert not st.patch_menu and actions[0][0] == "patch_cmds"
