"""Mixer with an app that has audio and MIDI (REAPER, Skrappjaw 2026-10-25): MIDI links never count as audio
peers, the app's MIDI gets its own row with an activity dot, and a JACK client's meter reads its own
output ports (pw-record --target on a JACK node falls back to the default input: it metered the mic)."""
import json
from pathlib import Path

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi

FIX = Path(__file__).parent / "fixtures"
REAPER_OUT = (1014, 1015)            # REAPER:out1, out2
CH_USB_IN = (71, 76)                 # GCS-8 CH USB playback_FL, FR


def dump():
    d = json.loads((FIX / "pwdump_midi.json").read_text())
    for k, (a, b) in enumerate(zip(REAPER_OUT, CH_USB_IN)):  # REAPER plays into CH USB, as live
        d.append({"id": 2000 + k, "type": "PipeWire:Interface:Link", "info": {
            "output-node-id": 1001, "output-port-id": a, "input-node-id": 143, "input-port-id": b, "state": "active"}})
    return d


DUMP = dump()
STREAMS = pi.streams(DUMP)
MX = pc.mixer_view(pi.inventory(DUMP), pcfg.default_config(), STREAMS)


def rows(label):
    return [r for r in STREAMS if r.label == label]


def test_midi_links_are_not_audio_peers():
    assert all("Midi-Bridge" not in r.inputs + r.outputs for r in STREAMS)
    assert all("Midi-Bridge" not in r.inputs + r.outputs for r in pi.inventory(DUMP).rows())
    (audio,) = rows("REAPER")  # playback only: nothing records into it as audio
    assert audio.direction == "out" and audio.outputs == ("GCS-8 CH USB",) and audio.midi == ()


def test_app_midi_gets_a_row_each_way():
    out = next(r for r in rows("REAPER · MIDI") if r.direction == "out")
    inn = next(r for r in rows("REAPER · MIDI") if r.direction == "in")
    assert out.outputs == ("KB16-01 MIDI 1",) and out.midi == (1013,)            # REAPER:MIDI Output 5
    assert inn.inputs == ("KB16-01 MIDI 1",) and inn.midi == (1011,)             # the KB16's capture port
    assert out.media == inn.media == "midi" and out.volume is None and out.mute is None and out.serial is None
    assert len({r.id for r in rows("REAPER") + rows("REAPER · MIDI")}) == 3       # keys never collide


def test_midi_row_sits_under_its_app():
    labels = [r.label for _, sec, r in pc.mixer_selectable(MX) if sec == "playback"]
    assert labels[labels.index("REAPER") + 1] == "REAPER · MIDI"


def test_no_listener_nodes_listed():
    d = DUMP + [{"id": 3000, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "node.name": "midi-dump", "media.class": "Midi/Sink"}}}]
    assert pi.is_meter({"node.name": "midi-dump"})
    assert not [r for r in pi.streams(d) if "midi-dump" in r.label]


def test_jack_meter_reads_its_own_output_ports():
    targets = pc.meter_targets(MX, 0)
    audio = rows("REAPER")[0]
    assert targets[("playback", audio.id)] == (audio.serial, False, REAPER_OUT)
    assert not [k for k in targets if k[1] in {r.id for r in rows("REAPER · MIDI")}]


def test_midi_targets_are_the_midi_rows():
    t = pc.midi_targets(MX, 0)
    assert {r.label for _, sec, r in pc.mixer_selectable(MX) if (sec, r.id) in t} == {"REAPER · MIDI"}
    assert sorted(t.values()) == [(1011,), (1013,)]


def screen(mx, **st):
    return pc.render_screen(pc.UIState(**st), {}, None, 120, 60, mixer=mx)


def midi_line(lines, arrow="→"):
    return next(t for t, _ in lines if f"REAPER · MIDI {arrow}" in t)


def test_dot_lights_after_an_event_and_fades():
    out = next(r for r in rows("REAPER · MIDI") if r.direction == "out")
    now = 100.0
    lit = {**MX, "midi_seen": {("playback", out.id): now - 0.1}, "now": now}
    old = {**MX, "midi_seen": {("playback", out.id): now - 5}, "now": now}
    assert "● MIDI" in midi_line(screen(lit)[0]) and "○ MIDI" in midi_line(screen(old)[0])
    assert "○ MIDI" in midi_line(screen(MX)[0]) and "no volume control" not in midi_line(screen(MX)[0])


def test_enter_on_a_midi_row_has_nothing_to_set():
    i = next(n for n, (_, sec, r) in enumerate(pc.mixer_selectable(MX)) if r.label == "REAPER · MIDI")
    st, actions = pc.handle_key(pc.UIState(mix_cursor=i), "enter", {}, mixer=MX)
    assert actions == [] and "nothing to set" in st.message


def test_meter_cmd_unlinked():
    cmd = pi.meter_cmd(None, False, name="pipewrench-meter-7")
    assert "--target" in cmd and cmd[cmd.index("--target") + 1] == "0"
    props = cmd[cmd.index("-P") + 1]
    assert "pipewrench-meter-7" in props and "node.autoconnect = false" in props
