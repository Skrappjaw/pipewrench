"""The Mixer tab: devices and app streams, with volume, mute and the default playback and recording device."""
import dataclasses
import json
from pathlib import Path

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi
from test_patch import with_reaper

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
DUMP = json.loads((FIX / "pwdump_patch.json").read_text())
CH2_IN = "alsa_input.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_in_GCS8_0_6_7__source"


def mx(dump=DUMP, config=None):
    return pc.mixer_view(pi.inventory(dump), config or pcfg.default_config(), pi.streams(dump))


MX = mx()


def screen(st, m=MX, width=100, height=60):
    return pc.render_screen(st, CUR, None, [], width, height, mixer=m)


def body(lines):
    return "\n".join(t for t, _ in lines)


def key(st, *keys, m=MX):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, [], mixer=m)
        actions += a
    return st, actions


def at(label, section="playback", m=MX):
    """The Mixer with its cursor on the first row with this label in that section."""
    i = next(n for n, (_, sec, r) in enumerate(pc.mixer_selectable(m)) if r.label == label and sec == section)
    return pc.UIState(tab="mixer", mix_cursor=i)


# --- model --------------------------------------------------------------------------------

def test_streams_are_apps_with_volume_and_their_device():
    rows = {(r.direction, r.label): r for r in pi.streams(DUMP)}
    play, rec = rows[("out", "Discord")], rows[("in", "Discord")]
    assert play.kind == rec.kind == "app" and play.volume == 1.0 and play.mute is False
    assert play.outputs == ("GCS-8 CH 4-AUX",) and rec.inputs == ("GCS-8 CH 2",)


def test_jack_clients_are_listed_without_volume():
    rows = [r for r in pi.streams(with_reaper(DUMP)) if r.label == "REAPER"]
    assert rows and all(r.volume is None and r.mute is None for r in rows)


def test_meter_and_peak_streams_are_never_listed():
    extra = [{"id": 900 + n, "type": "PipeWire:Interface:Node", "info": {"props": props, "params": {}}}
             for n, props in enumerate([{"media.class": "Stream/Input/Audio", "node.name": pi.METER_NAME + "-7"},
                                        {"media.class": "Stream/Input/Audio", "node.name": "pavu", "stream.monitor": True}])]
    assert {r.id for r in pi.streams(DUMP + extra)} == {r.id for r in pi.streams(DUMP)}


def test_mixer_leaves_out_hidden_rows_and_monitors():
    hidden = mx(config=pcfg.toggle_hidden(pcfg.default_config(), CH2_IN))
    assert CH2_IN not in [r.name for _, _, r in pc.mixer_selectable(hidden)]
    assert not any(r.kind == "monitor" for _, _, r in pc.mixer_selectable(MX))


# --- screen -------------------------------------------------------------------------------

def test_mixer_is_the_first_tab_and_the_default():
    assert pc.TABS[0] == "mixer" and pc.UIState().tab == "mixer"


def test_sections_and_default_stars():
    text = body(screen(pc.UIState())[0])
    assert text.index("Playback") < text.index("Recording")
    assert "Discord → GCS-8 CH 4-AUX" in text and "Discord ← GCS-8 CH 2" in text
    stars = [t for t in text.splitlines()[:-1] if "★" in t]  # the last line is the hint, which names ★
    assert len(stars) == 2  # one default output, one default input
    sink = next(r.label for _, _, r in pc.mixer_selectable(MX) if r.name == MX["defaults"]["sink"])
    assert sink in stars[0]


def test_volume_bar_over_100_reads_as_alert_and_mute_as_dim():
    loud = dataclasses.replace(MX["playback"][0][0], volume=1.3, mute=False)
    text, spans = pc._volume_bar(loud)
    assert text.endswith("130%") and any(r == "alert" for *_, r in spans)
    text, spans = pc._volume_bar(dataclasses.replace(loud, mute=True))
    assert text.endswith("muted") and {r for *_, r in spans} == {"dim"}
    assert "no volume control" in pc._volume_bar(dataclasses.replace(loud, volume=None))[0]


def test_cursor_bar_covers_the_name_not_the_volume():
    lines, _ = screen(at("Discord"))
    style = next(s for t, s in lines if "Discord →" in t)
    roles = [r for *_, r in style[1]]
    assert roles[0] == "selected" and "ok" in roles


def test_loading_before_the_first_dump():
    assert "loading the mixer" in body(pc.render_screen(pc.UIState(), CUR, None, [], 80, 20)[0])


def test_rows_fit_the_width():
    for width in (60, 100, 160):
        lines, hits = screen(pc.UIState(), width=width)
        assert all(len(t) <= width for t, _ in lines) and all(c1 <= width for _, _, c1, _ in hits)


# --- keys and mouse -------------------------------------------------------------------------

def test_arrows_step_volume_and_space_mutes():
    st = at("Discord")
    play = next(r for r in pi.streams(DUMP) if r.direction == "out")
    assert key(st, "right")[1] == [("mix_cmd", ["wpctl", "set-volume", str(play.id), "5%+"])]
    assert key(st, "left")[1] == [("mix_cmd", ["wpctl", "set-volume", str(play.id), "5%-"])]
    assert key(st, " ")[1] == [("mix_cmd", ["wpctl", "set-mute", str(play.id), "toggle"])]


def test_enter_makes_a_device_the_default_but_not_an_app():
    dev = pc.mixer_selectable(MX)[0][2]
    assert key(pc.UIState(mix_cursor=0), "enter")[1] == [("mix_cmd", ["wpctl", "set-default", str(dev.id)])]
    assert key(at("Discord"), "enter")[1] == []
    st, actions = key(at("Discord"), "d")
    assert actions == [] and st.message == st.failed


def test_no_volume_control_is_refused():
    m = mx(with_reaper(DUMP))
    st, actions = key(at("REAPER", m=m), "right", m=m)
    assert actions == [] and "no volume control" in st.message and st.message == st.failed


def test_wheel_changes_volume_and_double_click_is_enter():
    st = pc.UIState()
    _, hits = screen(st)
    row, c0, _, _ = next(h for h in hits if h[3] == ("mix_row", 0))
    _, actions = pc.handle_mouse(st, hits, row, c0, "wheel_up", CUR, [], mixer=MX)
    assert actions[0][1][1] == "set-volume" and actions[0][1][-1] == "5%+"
    _, actions = pc.handle_mouse(st, hits, row, c0, "double", CUR, [], mixer=MX)
    assert actions[0][1][1] == "set-default"


def test_up_down_clamp():
    n = len(pc.mixer_selectable(MX))
    st, _ = key(pc.UIState(), *["down"] * (n + 5))
    assert st.mix_cursor == n - 1
    st, _ = key(st, *["up"] * (n + 5))
    assert st.mix_cursor == 0


def test_bars_line_up_for_devices_and_apps():
    lines, _ = screen(pc.UIState())
    starts = {t.index("█") for t, _ in lines if "█" in t}
    assert len(starts) == 1


# --- live meters -------------------------------------------------------------------------------

def live_mx(states=None):
    """MX with serials, and the given labels running (everything else suspended)."""
    states = states or {}
    def fix(rows):
        return [dataclasses.replace(r, serial=1000 + r.id, state=states.get(r.label, "suspended")) for r in rows]
    return {**MX, "playback": tuple(fix(x) for x in MX["playback"]), "recording": tuple(fix(x) for x in MX["recording"])}


def test_meters_follow_running_rows_and_the_cursor():
    m = live_mx({"GCS-8 CH 4-AUX": "running", "Discord": "running", "GCS-8 CH 2": "running"})
    targets = pc.meter_targets(m, 0)
    labels = {(sec, r.label) for _, sec, r in pc.mixer_selectable(m) if (sec, r.id) in targets}
    first = pc.mixer_selectable(m)[0]
    assert (first[1], first[2].label) in labels                        # the selected row, though suspended
    assert {("playback", "GCS-8 CH 4-AUX"), ("playback", "Discord"), ("recording", "GCS-8 CH 2")} <= labels
    assert ("recording", "Discord") not in labels                        # a recording app has nothing to read
    sink = next(r for _, sec, r in pc.mixer_selectable(m) if r.label == "GCS-8 CH 4-AUX")
    assert targets[("playback", sink.id)] == (sink.serial, True)       # outputs are read from their monitor


def test_meters_are_capped():
    every = {r.label: "running" for _, _, r in pc.mixer_selectable(MX)}
    assert len(pc.meter_targets(live_mx(every), 0)) == pc.METER_CAP


def test_meter_level_jumps_up_and_falls_20_db_a_second():
    assert pc.meter_level(0.1, 0.5, 0.05) == 0.5
    assert abs(pc.meter_level(1.0, 0.0, 1.0) - 0.1) < 1e-9
    assert abs(pc.meter_level(1.0, 0.0, 1.0, fall=40) - 0.01) < 1e-9   # 40 dB in a second
    assert abs(pc.meter_level(1.0, 0.0, 0.5, fall=80) - 0.01) < 1e-9   # 80 dB/s: 40 dB in half


def test_meter_cells_colour_by_level():
    text, spans = pc._meter_cells(1.0)
    assert text == "█" * 16 and {"ok", "pending", "alert"} <= {r for *_, r in spans}
    text, spans = pc._meter_cells(0.0)
    assert text == "·" * 16
    text, _ = pc._meter_cells(10 ** (-12 / 20))  # -12 dBFS
    assert text.count("█") == 12


def test_meter_cmd_reads_a_monitor_for_outputs():
    cmd = pi.meter_cmd(42, True)
    assert cmd[:5] == ["stdbuf", "-o0", "pw-record", "--target", "42"] and cmd[-1] == "-"
    assert "stream.capture.sink = true" in cmd[cmd.index("-P") + 1] and pi.METER_NAME in cmd[cmd.index("-P") + 1]
    assert "stream.capture.sink" not in pi.meter_cmd(42, False)[pi.meter_cmd(42, False).index("-P") + 1]


def test_metered_rows_draw_cells_and_v_turns_them_off():
    m = live_mx({"Discord": "running"})
    disc = next(r for _, sec, r in pc.mixer_selectable(m) if r.label == "Discord" and sec == "playback")
    m = {**m, "metered": {("playback", disc.id): (disc.serial, False)}, "levels": {("playback", disc.id): 0.5}}
    lines, _ = screen(pc.UIState(), m=m)
    row = next(t for t, _ in lines if "Discord →" in t)
    assert row.rstrip().endswith("·") and "█" in row.split("%", 1)[1]
    st, _ = key(pc.UIState(), "v", m=m)
    assert not st.meters and st.message == "meters off"
    assert "·" not in next(t for t, _ in screen(st, m=m)[0] if "Discord →" in t)


def test_meter_streams_stay_out_of_patch_and_devices():
    import pwpatch as pp
    meter = {"id": 950, "type": "PipeWire:Interface:Node", "info": {"props": {
        "media.class": "Stream/Input/Audio", "node.name": pi.METER_NAME, "application.name": "pw-record"}, "params": {}}}
    link = {"id": 951, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 50, "input-node-id": 950,
                                                                  "output-port-id": 1, "input-port-id": 2}}
    d = DUMP + [meter, link]
    assert all(e.node != 950 for e in pp.graph(d).sources + pp.graph(d).dests)
    assert not any("pw-record" in r.outputs for r in pi.inventory(d).rows())
