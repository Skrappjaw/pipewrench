"""Patch tab with MIDI: a MIDI heading in both columns and in the grid, audio and MIDI never linked
from the keys, and the cursor landing on the source's own kind of destination."""
import json
from pathlib import Path

import pwcore as pc
import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
G = pp.graph(json.loads((FIX / "pwdump_midi.json").read_text()))
SRCS = pc.patch_sources(G, pc.UIState(tab="patch"))


def si(label, media="midi"):
    return next(i for i, s in enumerate(SRCS) if s.label == label and s.media == media)


def di(label, media="midi"):
    return next(i for i, d in enumerate(G.dests) if d.label == label and d.media == media)


def render(st, width=140, height=80):
    return pc.render_screen(st, {}, None, width, height, patch=G)


def key(st, *keys):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, {}, patch=G)
        actions += a
    return st, actions


def test_column_view_has_a_midi_heading_on_both_sides():
    lines, hits = render(pc.UIState(tab="patch"))
    left = [t.split("│")[0].strip() for t, _ in lines]
    right = [t.split("│")[1].strip() if "│" in t else "" for t, _ in lines]
    head = left.index("MIDI")
    assert head < next(i for i, t in enumerate(left) if "KB16-01 MIDI 1" in t)
    assert "title" in str(lines[head][1])
    assert not [h for h in hits if h[0] == head and h[3][0] == "patch_src"]
    assert "MIDI" in right and right.index("MIDI") < next(i for i, t in enumerate(right) if "nanoKONTROL2" in t)


def test_kb16_shows_its_link_count():
    lines, _ = render(pc.UIState(tab="patch"))
    row = next(t for t, _ in lines if "KB16-01 MIDI 1" in t.split("│")[0])
    assert "●1" in row.split("│")[0]


def test_grid_view_has_the_midi_heading():
    lines, _ = render(pc.UIState(tab="patch", patch_view="grid"))
    text = [t for t, _ in lines]
    head = next(i for i, t in enumerate(text) if t.strip() == "MIDI")
    assert head < next(i for i, t in enumerate(text) if "KB16-01 MIDI 1" in t and "[" not in t[:2])


def test_enter_on_audio_to_midi_explains_and_links_nothing():
    st = pc.UIState(tab="patch", patch_src=si("KB16-01 MIDI 1"), patch_dst=0, patch_side="dst")
    assert G.dests[0].media == "audio"
    st, actions = key(st, "enter")
    assert actions == [] and "MIDI connects only to MIDI" in st.message


def test_port_matrix_refuses_audio_to_midi():
    st = pc.UIState(tab="patch", patch_src=si("KB16-01 MIDI 1"), patch_dst=0, patch_side="dst", patch_ports=True)
    st, actions = key(st, "enter")
    assert actions == [] and "MIDI connects only to MIDI" in st.message


def test_moving_to_destinations_lands_on_midi():
    st = pc.UIState(tab="patch", patch_src=si("nanoKONTROL2 _ CTRL"), patch_dst=0)
    st, _ = key(st, "right")
    assert G.dests[st.patch_dst].media == "midi"
    st = pc.UIState(tab="patch", patch_src=si("KB16-01 MIDI 1"), patch_dst=0)
    st, _ = key(st, "right")
    assert G.dests[st.patch_dst].label == "REAPER" and G.dests[st.patch_dst].media == "midi"


def test_enter_links_a_midi_pair():
    st = pc.UIState(tab="patch", patch_src=si("nanoKONTROL2 _ CTRL"), patch_dst=di("REAPER"), patch_side="dst")
    _, actions = key(st, "enter")
    assert actions == [("patch_cmds", [["pw-link", "1007", "1012"]])]


def test_pair_status_for_a_mixed_pair_says_why():
    st = pc.UIState(tab="patch", patch_src=si("KB16-01 MIDI 1"), patch_dst=0, patch_side="dst")
    lines, _ = render(st)
    assert any("MIDI connects only to MIDI" in t for t, _ in lines)
