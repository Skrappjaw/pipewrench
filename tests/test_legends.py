"""Every tab shows its keys the same way: a box just above the status line, each key a bold inverted chip
with its slug beside it, fitting the screen width. The status line carries messages, not keys."""
import json
import re
from pathlib import Path

import pytest

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi
import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
DUMP = json.loads((FIX / "pwdump_patch.json").read_text())
DEV = pc.devices_view(pi.inventory(DUMP), pcfg.default_config())
PATCH = pp.graph(DUMP)
PRESETS = [{"name": "mix", "label": "Mixing", "values": {"clock.force-quantum": 1024}}]

STATES = {
    "settings": pc.UIState(tab="settings"),
    "top": pc.UIState(tab="top"),
    "presets": pc.UIState(tab="settings", cursor=pc.THEME_ROW + 1),  # a preset row inside Settings
    "devices": pc.UIState(tab="devices"),
    "devices menu": pc.UIState(tab="devices", dev_menu=("card", 95), dev_cursor=5),
    "patch columns": pc.UIState(tab="patch"),
    "patch grid": pc.UIState(tab="patch", patch_view="grid"),
    "patch ports": pc.UIState(tab="patch", patch_ports=True, patch_side="dst"),
}
ENTER = {"settings": "apply", "presets": "apply", "devices": "actions", "devices menu": "pick",
         "patch columns": "connect/disconnect", "patch grid": "connect/disconnect", "patch ports": "link/unlink"}


def render(st, width=80, height=30):
    return pc.render_screen(st, CUR, [], PRESETS, width, height, devices=DEV, patch=PATCH)[0]


def box(lines):
    """(top index, bottom index) of the key box."""
    top = next(i for i, (t, _) in enumerate(lines) if t.startswith("┌─ Keys"))
    bottom = next(i for i in range(top + 1, len(lines)) if lines[i][0].startswith("└"))
    return top, bottom


def chips(lines):
    """[(key, slug)] read off the box: each chip's text, and the words after it up to the next chip."""
    top, bottom = box(lines)
    out = []
    for text, style in lines[top + 1:bottom]:
        assert isinstance(style, tuple) and all(r == "chip" for *_, r in style[1]), style
        spans = [(c0, c1) for c0, c1, _ in style[1]]
        for n, (c0, c1) in enumerate(spans):
            end = spans[n + 1][0] if n + 1 < len(spans) else len(text) - 1
            out.append((text[c0:c1].strip(), text[c1:end].strip(" │")))
    return out


@pytest.mark.parametrize("name", list(STATES))
def test_key_box_sits_just_above_the_status_line(name):
    lines = render(STATES[name])
    top, bottom = box(lines)
    assert bottom == len(lines) - 2
    assert all(t.startswith("│") and t.rstrip().endswith("│") for t, _ in lines[top + 1:bottom])


@pytest.mark.parametrize("width", [80, 120])
@pytest.mark.parametrize("name", list(STATES))
def test_box_fits_the_width_and_edges_line_up(name, width):
    lines = render(STATES[name], width=width)
    top, bottom = box(lines)
    widths = {len(t) for t, _ in lines[top:bottom + 1]}
    assert widths == {width}, widths


@pytest.mark.parametrize("name", list(STATES))
def test_keys_are_chips_with_their_slug(name):
    pairs = chips(render(STATES[name]))
    assert ("Enter", ENTER[name]) in pairs if name in ENTER else pairs
    assert all(k and s for k, s in pairs), pairs


@pytest.mark.parametrize("name", [n for n in STATES if n != "devices menu"])
def test_every_box_ends_with_tabs_and_quit(name):
    pairs = chips(render(STATES[name]))
    assert pairs[-2:] == [(f"Tab/1-{len(pc.TABS)}", "tabs"), ("q", "quit")]


def test_enter_comes_first_where_it_acts():
    for name in ENTER:
        assert chips(render(STATES[name]))[0][0] == "Enter", name


@pytest.mark.parametrize("name", list(STATES))
def test_one_box_and_a_status_line_for_messages(name):
    st = STATES[name]
    lines = render(st, height=60)
    assert sum(t.startswith("┌─ Keys") for t, _ in lines) == 1
    assert render(pc.dataclasses.replace(st, message="saved"))[-1][0].strip() == "saved"
    assert "quit" not in render(st)[-1][0]


def test_legend_survives_a_message():
    assert ("Enter", "apply") in chips(render(pc.UIState(tab="settings", message="applied")))


def flat(lines):
    """Box text with chip padding collapsed, for 'Enter actions'-style checks in other test files."""
    return re.sub(r"\s+", " ", "\n".join(t for t, _ in lines))
