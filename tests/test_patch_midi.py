"""MIDI in the patch graph: ALSA MIDI ports (all on PipeWire's one Midi-Bridge node) and JACK clients'
MIDI ports, from a live dump taken with REAPER open (KB16 ↔ REAPER linked both ways)."""
import json
from pathlib import Path

import pytest

import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
AUDIO_ONLY = json.loads((FIX / "pwdump_patch.json").read_text())
DUMP = json.loads((FIX / "pwdump_midi.json").read_text())


@pytest.fixture
def g():
    return pp.graph(DUMP)


def midi(items):
    return [e for e in items if e.media == "midi"]


def one(items, label):
    return next(e for e in items if e.label == label)


def test_bridge_ports_are_one_endpoint_each_named_by_alias(g):
    labels = [e.label for e in midi(g.sources)]
    assert labels == ["REAPER", "GCS-8 MIDI 1", "GCS-8 MIDI 2", "KB16-01 MIDI 1", "nanoKONTROL2 _ CTRL"]  # apps first, as audio
    assert [e.label for e in midi(g.dests)] == labels
    nano = one(midi(g.sources), "nanoKONTROL2 _ CTRL")
    assert nano.kind == "device" and nano.name == "nanoKONTROL2:nanoKONTROL2 _ CTRL" and len(nano.ports) == 1


def test_midi_through_is_left_out(g):
    assert not any("Midi Through" in e.label for e in g.sources + g.dests)


def test_jack_client_midi_port_is_its_own_app_endpoint(g):
    r = one(midi(g.sources), "REAPER")
    assert r.kind == "app" and r.name == "REAPER:MIDI Output 5"
    assert one(midi(g.dests), "REAPER").name == "REAPER:MIDI Input 5"
    audio = [e for e in g.sources if e.label == "REAPER" and e.media == "audio"]
    assert len(audio) == 1 and all(p.channel != "MIDI" for p in audio[0].ports)


def test_a_client_with_several_midi_ports_names_each(g):
    dump = json.loads(json.dumps(DUMP))
    extra = json.loads(json.dumps(next(o for o in dump if o["id"] == 1012)))
    extra["id"] = 1100
    extra["info"]["props"]["port.name"] = "MIDI Input 6"
    extra["info"]["props"]["port.alias"] = "REAPER:MIDI Input 6"
    g2 = pp.graph(dump + [extra])
    assert [e.label for e in midi(g2.dests) if e.kind == "app"] == ["REAPER · MIDI Input 5", "REAPER · MIDI Input 6"]


def test_midi_sorts_after_all_audio(g):
    for items in (g.sources, g.dests):
        kinds = [e.media for e in items]
        assert kinds == sorted(kinds, key=lambda m: m == "midi")
    assert g.sources.index(midi(g.sources)[0]) > max(i for i, e in enumerate(g.sources) if e.monitor)


def test_audio_graph_unchanged_by_midi():
    base, both = pp.graph(AUDIO_ONLY), pp.graph(DUMP)
    audio = lambda items: [(e.label, e.monitor) for e in items if e.media == "audio" and e.label != "REAPER"]
    assert audio(both.sources) == audio(base.sources) and audio(both.dests) == audio(base.dests)
    assert not midi(base.sources) and not midi(base.dests)


def test_live_midi_links_read(g):
    kb = one(midi(g.sources), "KB16-01 MIDI 1")
    assert [d.label for d in g.targets(kb)] == ["REAPER"]
    assert g.state(kb, one(midi(g.dests), "REAPER")) == "full"
    assert g.state(one(midi(g.sources), "REAPER"), one(midi(g.dests), "KB16-01 MIDI 1")) == "full"


def test_audio_and_midi_never_connect(g):
    kb = one(midi(g.sources), "KB16-01 MIDI 1")
    audio_dst = next(d for d in g.dests if d.media == "audio")
    assert g.state(kb, audio_dst) == "n/a"
    assert pp.toggle_cmds(g, kb, audio_dst) == [] and pp.connect_cmds(g, kb, audio_dst) == []
    audio_src = next(s for s in g.sources if s.media == "audio")
    assert g.state(audio_src, one(midi(g.dests), "REAPER")) == "n/a"


def test_connect_and_disconnect_a_midi_pair(g):
    nano, reaper = one(midi(g.sources), "nanoKONTROL2 _ CTRL"), one(midi(g.dests), "REAPER")
    assert pp.toggle_cmds(g, nano, reaper) == [["pw-link", str(nano.ports[0].id), "1012"]]
    kb = one(midi(g.sources), "KB16-01 MIDI 1")
    assert pp.toggle_cmds(g, kb, reaper) == [["pw-link", "-d", "1018"]]
    assert pp.slots(nano, reaper) is None


def test_find_by_alias_and_by_media(g):
    assert g.find_source("nanoKONTROL2:nanoKONTROL2 _ CTRL").label == "nanoKONTROL2 _ CTRL"
    assert g.find_source("nanoKONTROL2 _ CTRL").media == "midi"
    assert g.find_dest("REAPER", media="midi").name == "REAPER:MIDI Input 5"
    assert g.find_source("REAPER").media == "audio"  # its node.name; the MIDI ports go by alias
    assert g.find_source("REAPER:MIDI Output 5").media == "midi"
    assert g.find_source("1000") is None  # the bridge's node id names no one device


def test_no_slot_menu_between_audio_and_midi(g):
    nano = one(midi(g.sources), "nanoKONTROL2 _ CTRL")
    many_inputs = pp.Endpoint(1, False, "REAPER", "REAPER", "app",
                              tuple(pp.Port(900 + k, 1, f"in{k}", f"in{k}", "input") for k in range(8)))
    assert pp.slots(nano, many_inputs) is None
