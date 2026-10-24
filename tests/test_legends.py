"""Every tab shows its keys the same way: a legend just above the status line, at most two lines, each
fitting 80 columns, listed once. The status line carries messages, not keys."""
import json
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
    "presets": pc.UIState(tab="presets"),
    "devices": pc.UIState(tab="devices"),
    "devices menu": pc.UIState(tab="devices", dev_menu=("card", 95), dev_cursor=5),
    "patch columns": pc.UIState(tab="patch"),
    "patch grid": pc.UIState(tab="patch", patch_view="grid"),
    "patch ports": pc.UIState(tab="patch", patch_ports=True, patch_side="dst"),
}
ENTER = {"settings": "Enter apply", "presets": "Enter apply", "devices": "Enter actions",
         "devices menu": "Enter pick", "patch columns": "Enter connect", "patch grid": "Enter connect",
         "patch ports": "Enter link"}


def render(st, width=80, height=30):
    return pc.render_screen(st, CUR, [], PRESETS, width, height, devices=DEV, patch=PATCH)[0]


def legend_lines(lines):
    return [i for i, (t, _) in enumerate(lines) if t.startswith("  Keys: ") or (t.startswith("        ") and "quit" in t)]


@pytest.mark.parametrize("name", list(STATES))
def test_legend_sits_just_above_the_status_line(name):
    lines = render(STATES[name])
    rows = legend_lines(lines)
    assert rows and rows[-1] == len(lines) - 2, (name, [t for t, _ in lines[-4:]])
    assert lines[rows[0]][0].startswith("  Keys: ")
    assert len(rows) <= 2


@pytest.mark.parametrize("name", list(STATES))
def test_legend_fits_80_and_says_what_enter_does(name):
    lines = render(STATES[name])
    text = "\n".join(lines[i][0] for i in legend_lines(lines))
    assert all(len(lines[i][0].rstrip()) <= 80 for i in legend_lines(lines))
    if name in ENTER:
        assert ENTER[name] in text, (name, text)


@pytest.mark.parametrize("name", [n for n in STATES if n != "devices menu"])
def test_legend_ends_with_tabs_and_quit(name):
    lines = render(STATES[name])
    assert lines[legend_lines(lines)[-1]][0].rstrip().endswith("Tab/1-5 tabs  q quit")


@pytest.mark.parametrize("name", list(STATES))
def test_keys_are_listed_once(name):
    lines = render(STATES[name], height=60)
    assert sum("Keys:" in t for t, _ in lines) == 1
    assert sum("q quit" in t for t, _ in lines) <= 1


@pytest.mark.parametrize("name", list(STATES))
def test_status_line_is_for_messages(name):
    st = STATES[name]
    assert render(pc.dataclasses.replace(st, message="saved"))[-1][0].strip() == "saved"
    assert "Keys" not in render(st)[-1][0] and "q quit" not in render(st)[-1][0]


def test_legend_survives_a_message():
    lines = render(pc.UIState(tab="settings", message="applied"))
    assert any("Enter apply" in t for t, _ in lines)
