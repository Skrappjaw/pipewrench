"""Devices tab MIDI heading: one read-only row per ALSA MIDI port, after the cards and before Virtual,
from a live dump with REAPER linked to the KB16 both ways."""
import io
import json
from pathlib import Path

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi
import pipewrench

FIX = Path(__file__).parent / "fixtures"
DUMP = json.loads((FIX / "pwdump_midi.json").read_text())
INV = pi.inventory(DUMP)


def by_label(label):
    return next(r for r in INV.midi if r.label == label)


def test_one_row_per_alsa_midi_port_without_midi_through():
    assert [r.label for r in INV.midi] == ["GCS-8 MIDI 1", "GCS-8 MIDI 2", "KB16-01 MIDI 1", "nanoKONTROL2 _ CTRL"]
    assert all(r.kind == "midi" for r in INV.midi)


def test_both_ports_make_it_duplex_with_its_alsa_card():
    nano = by_label("nanoKONTROL2 _ CTRL")
    assert nano.direction == "duplex" and nano.alsa_card == 3 and nano.name == "nanoKONTROL2:nanoKONTROL2 _ CTRL"


def test_links_read_as_connections():
    kb = by_label("KB16-01 MIDI 1")
    assert kb.inputs == ("REAPER",) and kb.outputs == ("REAPER",)
    assert pi.connections(kb) == "← REAPER  → REAPER"
    assert pi.connections(by_label("GCS-8 MIDI 1")) == ""


def test_midi_stays_out_of_rows_and_finds():
    assert not [r for r in INV.rows() if r.kind == "midi"]
    assert INV.find("KB16-01 MIDI 1") is None


def test_view_puts_midi_after_the_cards_before_virtual():
    lines = pi.view(INV)
    heads = [h for h, r in lines if r is None]
    assert heads.index("MIDI") == heads.index("Virtual") - 1
    at = [h for h, _ in lines].index("MIDI")
    assert [r.label for _, r in lines[at + 1:at + 5]] == [r.label for r in INV.midi]


def test_no_heading_without_midi():
    inv = pi.inventory(json.loads((FIX / "pwdump_inventory.json").read_text()))
    assert inv.midi == [] and "MIDI" not in [h for h, r in pi.view(inv) if r is None]


def devices():
    return pc.devices_view(INV, pcfg.default_config())


def test_tui_lists_midi_read_only():
    lines, hits = pc.render_screen(pc.UIState(tab="devices"), {}, None, 120, 80, devices=devices())
    text = [t for t, _ in lines]
    head = next(i for i, t in enumerate(text) if t.startswith("┌─ MIDI "))
    kb = next(i for i, t in enumerate(text) if "KB16-01 MIDI 1" in t)
    assert head < kb < next(i for i, t in enumerate(text) if t.startswith("┌─ Buses "))
    assert "title" in str(lines[head][1]) and "hw:7" in text[kb] and "← REAPER  → REAPER" in text[kb]
    assert [h for h in hits if h[0] == kb and h[3][0] == "device"]       # a row now, with its note
    i = next(n for n, (k, o) in enumerate(pc.dev_selectable(devices(), pc.UIState(tab="devices")))
             if getattr(o, "kind", None) == "midi")
    st = pc.UIState(tab="devices", dev_cursor=i)
    assert "Patch (3)" in pc.render_screen(st, {}, None, 120, 80, devices=devices())[0][-1][0]


def test_cli_lists_midi():
    class Runner:
        def __call__(self, argv):
            return (0, json.dumps(DUMP)) if argv == ["pw-dump"] else (0, "")
    out = io.StringIO()
    assert pipewrench.main(["devices"], runner=Runner(), out=out, err=io.StringIO()) == 0
    text = out.getvalue().splitlines()
    at = text.index("MIDI")
    nano = next(t for t in text[at:] if "nanoKONTROL2" in t)
    assert "duplex" in nano and "hw:3" in nano
