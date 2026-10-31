import json
from pathlib import Path

import pytest

import pwinventory as pi
import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
CH2_IN = "alsa_input.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_in_GCS8_0_6_7__source"
CH4_AUX = "alsa_output.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_out_GCS8_0_10_11__sink"
CH3_AUX = "alsa_output.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_out_GCS8_0_8_9__sink"
ROCKSMITH = "alsa_input.usb-Hercules_Rocksmith_USB_Guitar_Adapter-00.mono-fallback"


def dump():
    return json.loads((FIX / "pwdump_patch.json").read_text())


@pytest.fixture
def g():
    return pp.graph(dump())


def src(g, label, monitor=False):
    return next(s for s in g.sources if s.label == label and s.monitor == monitor)


def dst(g, label):
    return next(d for d in g.dests if d.label == label)


# --- naming (shared with the Devices connections column) ---------------------------

@pytest.mark.parametrize("props, name", [
    ({"application.name": "WEBRTC VoiceEngine", "application.process.binary": "Discord"}, "Discord"),
    ({"application.name": "Brave", "application.process.binary": "brave"}, "Brave"),
    ({"application.name": "cinnamon", "application.process.binary": "python3.12"}, "cinnamon"),
    ({"node.description": "GCS-8 CH 2", "node.name": "x"}, "GCS-8 CH 2"),
    ({"node.name": "x"}, "x"),
])
def test_display_name(props, name):
    assert pi.display_name(props) == name


def test_devices_connections_use_the_same_names():
    inv = pi.inventory(dump())
    assert inv.find(CH4_AUX).inputs == ("Discord",) and inv.find(CH2_IN).outputs == ("Discord",)


# --- graph ----------------------------------------------------------------------------

def test_sources_apps_then_devices_monitors_separate(g):
    plain = [s.label for s in g.sources if not s.monitor]
    assert plain[0] == "Discord" and "GCS-8 CH 2" in plain and "Rocksmith Guitar Adapter Mono" in plain
    assert all(s.kind == "app" for s in g.sources[:1])
    mons = [s for s in g.sources if s.monitor]
    assert mons and all(s.label.endswith("(monitor)") for s in mons)
    assert "GCS-8 CH 4-AUX (monitor)" in [s.label for s in mons]


def test_destinations_include_apps_recording_and_device_outputs(g):
    labels = [d.label for d in g.dests]
    assert labels[0] == "Discord" and "GCS-8 CH 4-AUX" in labels and "CH1-OFF" in labels
    assert not any("(monitor)" in x for x in labels)


def test_ports_are_in_channel_order(g):
    assert [p.channel for p in src(g, "GCS-8 CH 2").ports] == ["FL", "FR"]
    assert [p.channel for p in dst(g, "GCS-8 CH 4-AUX").ports] == ["FL", "FR"]
    assert len(src(g, "Rocksmith Guitar Adapter Mono").ports) == 1


def test_connection_state(g):
    assert g.state(src(g, "Discord"), dst(g, "GCS-8 CH 4-AUX")) == "full"
    assert g.state(src(g, "GCS-8 CH 2"), dst(g, "Discord")) == "full"
    assert g.state(src(g, "GCS-8 CH 1"), dst(g, "GCS-8 CH 4-AUX")) == "none"
    assert [d.label for d in g.targets(src(g, "Discord"))] == ["GCS-8 CH 4-AUX"]


def test_partial_state_when_one_channel_is_linked(g):
    d = dump()
    s, t = src(g, "GCS-8 CH 1"), dst(g, "GCS-8 CH 3-AUX")
    d.append({"id": 9001, "type": "PipeWire:Interface:Link",
              "info": {"output-port-id": s.ports[0].id, "input-port-id": t.ports[0].id, "state": "active"}})
    g2 = pp.graph(d)
    assert g2.state(src(g2, "GCS-8 CH 1"), dst(g2, "GCS-8 CH 3-AUX")) == "partial"


def test_find_endpoints(g):
    assert g.find_source("Discord").label == "Discord"
    assert g.find_source("GCS-8 CH USB (monitor)").monitor
    assert g.find_source(CH2_IN).label == "GCS-8 CH 2"
    assert g.find_dest("gcs-8 ch 4-aux").node == g.find_dest(CH4_AUX).node
    assert g.find_source("nope") is None


# --- pairing and commands --------------------------------------------------------------

def test_pairs_stereo_by_channel(g):
    pairs = pp.pair_ports(src(g, "GCS-8 CH 1").ports, dst(g, "GCS-8 CH 3-AUX").ports)
    assert [(a.channel, b.channel) for a, b in pairs] == [("FL", "FL"), ("FR", "FR")]


def test_pairs_mono_to_both_and_both_to_mono(g):
    mono = src(g, "Rocksmith Guitar Adapter Mono").ports
    stereo = dst(g, "GCS-8 CH 3-AUX").ports
    assert [(a.channel, b.channel) for a, b in pp.pair_ports(mono, stereo)] == [("MONO", "FL"), ("MONO", "FR")]
    mono_in = [p for p in dst(g, "DualShock 4 [CUH-ZCT2x] Analog Stereo").ports][:1]
    assert len(pp.pair_ports(src(g, "GCS-8 CH 1").ports, mono_in)) == 2


def test_connect_skips_existing_and_disconnect_names_the_port_pair(g):
    s, t = src(g, "GCS-8 CH 1"), dst(g, "GCS-8 CH 3-AUX")
    cmds = pp.connect_cmds(g, s, t)
    assert cmds == [["pw-link", str(s.ports[0].id), str(t.ports[0].id)],
                    ["pw-link", str(s.ports[1].id), str(t.ports[1].id)]]
    assert pp.connect_cmds(g, src(g, "Discord"), dst(g, "GCS-8 CH 4-AUX")) == []
    dis = pp.disconnect_cmds(g, src(g, "Discord"), dst(g, "GCS-8 CH 4-AUX"))
    linked = [(a, b) for (a, b) in g.links if any(c[2:] == [str(a), str(b)] for c in dis)]
    assert len(dis) == 2 and all(c[:2] == ["pw-link", "-d"] and len(c) == 4 for c in dis)
    assert len(linked) == 2  # by output and input port id, never a link id from the snapshot
    assert pp.disconnect_cmds(g, s, t) == []


def test_toggle_connects_or_disconnects(g):
    assert pp.toggle_cmds(g, src(g, "GCS-8 CH 1"), dst(g, "GCS-8 CH 3-AUX"))[0][1] != "-d"
    assert pp.toggle_cmds(g, src(g, "Discord"), dst(g, "GCS-8 CH 4-AUX"))[0][:2] == ["pw-link", "-d"]


def test_port_level_pairs_and_toggle(g):
    s, t = src(g, "GCS-8 CH 1"), dst(g, "GCS-8 CH 3-AUX")
    rows = pp.port_pairs(g, s, t)
    assert [(a.channel, b.channel, linked) for a, b, linked in rows] == [
        ("FL", "FL", False), ("FL", "FR", False), ("FR", "FL", False), ("FR", "FR", False)]
    assert pp.port_toggle_cmd(g, rows[1][0], rows[1][1]) == ["pw-link", str(rows[1][0].id), str(rows[1][1].id)]
    d0, d1, _ = pp.port_pairs(g, src(g, "Discord"), dst(g, "GCS-8 CH 4-AUX"))[0]
    assert pp.port_toggle_cmd(g, d0, d1) == ["pw-link", "-d", str(d0.id), str(d1.id)]


def test_graph_tolerates_junk():
    g = pp.graph([{"type": "PipeWire:Interface:Port", "id": 1, "info": None},
                  {"type": "PipeWire:Interface:Link", "id": 2, "info": {"output-port-id": 99, "input-port-id": 98}}])
    assert g.sources == [] and g.dests == []


# --- JACK clients (e.g. REAPER via pw-jack): no media.class, ports without channel names -----

def with_reaper(d):
    """REAPER as pipewire-jack shows it on a real machine: media.type Audio, client.api jack, mono ports."""
    d = list(d)
    d.append({"id": 700, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "media.type": "Audio", "node.name": "REAPER", "node.description": "REAPER", "client.api": "jack",
        "media.category": "Duplex"}, "params": {}}})
    for pid, direction, name in [(701, "output", "out1"), (702, "output", "out2")] + \
            [(710 + n, "input", f"in{n}") for n in range(1, 9)] + [(720, "output", "midi_out")]:
        dsp = "8 bit raw midi" if "midi" in name else "32 bit float mono audio"
        d.append({"id": pid, "type": "PipeWire:Interface:Port", "info": {"direction": direction, "props": {
            "node.id": 700, "port.name": name, "format.dsp": dsp, "port.alias": f"REAPER:{name}"}}})
    return d


def test_jack_client_is_a_source_and_destination():
    g = pp.graph(with_reaper(dump()))
    s, d = g.find_source("REAPER"), g.find_dest("REAPER")
    assert s.kind == "app" and d.kind == "app"
    assert [p.name for p in s.ports] == ["out1", "out2"]           # MIDI port left out
    assert [p.name for p in d.ports] == [f"in{n}" for n in range(1, 9)]  # natural order, not in1, in10…


def test_jack_ports_pair_by_position():
    g = pp.graph(with_reaper(dump()))
    out = pp.pair_ports(g.find_source("REAPER").ports, g.find_dest("GCS-8 CH 3-AUX").ports)
    assert [(a.name, b.channel) for a, b in out] == [("out1", "FL"), ("out2", "FR")]
    rec = pp.pair_ports(g.find_source("GCS-8 CH 2").ports, g.find_dest("REAPER").ports)
    assert [(a.channel, b.name) for a, b in rec] == [("FL", "in1"), ("FR", "in2")]


def test_jack_client_does_not_become_a_device():
    inv = pi.inventory(with_reaper(dump()))
    assert inv.find("REAPER") is None


# --- slots: where a source lands on unnamed inputs ------------------------------------------

def test_slots_on_unnamed_inputs_are_aligned_runs_sized_to_the_source():
    g = pp.graph(with_reaper(dump()))
    d = g.find_dest("REAPER")
    runs = pp.slots(g.find_source("GCS-8 CH 2"), d)
    assert [[p.name for p in r] for r in runs] == [["in1", "in2"], ["in3", "in4"], ["in5", "in6"], ["in7", "in8"]]
    mono = next(s for s in g.sources if len(s.ports) == 1 and not s.monitor)
    assert len(pp.slots(mono, d)) == 8


def test_no_slots_where_channel_names_decide():
    g = pp.graph(with_reaper(dump()))
    assert pp.slots(g.find_source("REAPER"), g.find_dest("GCS-8 CH 3-AUX")) is None


def test_slot_cmds_link_into_a_later_slot_and_count_as_full():
    d = with_reaper(dump())
    g = pp.graph(d)
    s, r = g.find_source("GCS-8 CH 2"), g.find_dest("REAPER")
    third = pp.slots(s, r)[2]
    cmds = pp.slot_cmds(g, s, third)
    assert [c[2] for c in cmds] == [str(p.id) for p in third]
    for n, c in enumerate(cmds):
        d.append({"id": 9000 + n, "type": "PipeWire:Interface:Link",
                  "info": {"output-port-id": int(c[1]), "input-port-id": int(c[2]), "state": "active"}})
    g = pp.graph(d)
    s, r = g.find_source("GCS-8 CH 2"), g.find_dest("REAPER")
    assert g.state(s, r) == "full"
    runs = pp.slots(s, r)
    assert g.slot_sources(runs[2]) == [s] and g.first_free(runs) == 0
    drop = pp.slot_cmds(g, s, runs[2])  # picking it again disconnects, by port pair
    assert drop and all(c[1] == "-d" and (int(c[2]), int(c[3])) in g.links for c in drop)
