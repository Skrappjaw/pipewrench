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
    return pc.render_screen(st, CUR, None, width, height, mixer=m)


def body(lines):
    return "\n".join(t for t, _ in lines)


def key(st, *keys, m=MX):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR, mixer=m)
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


def test_mixer_leaves_out_the_rows_of_a_hidden_card():
    ds4 = next(c for c in pi.inventory(DUMP).cards if c.label.startswith("DualShock"))
    hidden = mx(config=pcfg.toggle_hidden(pcfg.default_config(), ds4.name))
    names = [r.name for _, _, r in pc.mixer_selectable(hidden)]
    assert ds4.rows and not any(r.name in names for r in ds4.rows)


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
    assert text.startswith("130%") and any(r == "vol_over" for *_, r in spans)
    text, spans = pc._volume_bar(dataclasses.replace(loud, mute=True))
    assert text.startswith("muted") and {r for *_, r in spans} <= {"dim", "bar_dim"}
    assert "no volume control" in pc._volume_bar(dataclasses.replace(loud, volume=None))[0]


def test_cursor_bar_covers_the_name_not_the_volume():
    lines, _ = screen(at("Discord"))
    style = next(s for t, s in lines if "Discord →" in t)
    roles = [r for *_, r in style[1]][2:]  # after the box frame's two edges (QA pass, 2026-10-26)
    assert roles[0] == "bar_src" and "vol" in roles  # the playback cursor: blue in pipeOS, the plain cursor elsewhere


def test_loading_before_the_first_dump():
    assert "loading the mixer" in body(pc.render_screen(pc.UIState(), CUR, None, 80, 20)[0])


def test_rows_fit_the_width():
    for width in (60, 100, 160):
        lines, hits = screen(pc.UIState(), width=width)
        assert all(len(t) <= width for t, _ in lines) and all(c1 <= width for _, _, c1, _ in hits)


# --- keys and mouse -------------------------------------------------------------------------

def test_arrows_step_volume_and_space_mutes():
    st, m, nid = discord_at(0.5)
    assert key(st, "right", m=m)[1] == [("mix_cmd", ["wpctl", "set-volume", "--limit", "1", str(nid), "5%+"])]
    assert key(st, "left", m=m)[1] == [("mix_cmd", ["wpctl", "set-volume", "--limit", "1.5", str(nid), "5%-"])]
    assert key(st, " ", m=m)[1] == [("mix_cmd", ["wpctl", "set-mute", str(nid), "toggle"])]


def labels(st, m=MX):
    return [label for label, _ in pc.mix_menu_items(m, st)]


def row_at(st, m=MX):
    return pc.mixer_selectable(m)[st.mix_cursor][2]


def test_enter_opens_a_menu_and_enter_again_sets_the_system_default():
    st, actions = key(at("GCS-8 CH 3-AUX"), "enter")
    assert actions == [] and st.mix_menu == ("playback", row_at(st).id)
    assert labels(st) == ["Set as system default", "Mute", "Test sound", "Rename…", "Card profile…", "Volume boost: locked", "Back"]
    st, actions = key(st, "enter")
    assert actions == [("mix_cmd", ["wpctl", "set-default", str(row_at(st).id)])] and st.mix_menu is None


def test_the_current_default_says_so_and_picking_it_just_closes():
    st, _ = key(at("GCS-8 CH USB"), "enter")
    assert labels(st)[0] == "★ System default"
    st, actions = key(st, "enter")
    assert actions == [] and st.mix_menu is None


def test_inputs_have_no_test_sound():
    st, _ = key(at("GCS-8 CH 3", "recording"), "enter")
    assert labels(st) == ["Set as system default", "Mute", "Rename…", "Card profile…", "Volume boost: locked", "Back"]


def test_an_app_menu_mutes_and_d_is_no_longer_a_shortcut():
    st, _ = key(at("Discord"), "enter")
    assert labels(st) == ["Mute", "Volume boost: locked", "Back"]
    _, actions = key(st, "enter")
    assert actions == [("mix_cmd", ["wpctl", "set-mute", str(row_at(st).id), "toggle"])]
    for target in ("Discord", "GCS-8 CH 3-AUX"):
        st, actions = key(at(target), "d")
        assert actions == [] and st.mix_menu is None


def test_menu_items_map_to_their_actions():
    st, _ = key(at("GCS-8 CH 3-AUX"), "enter")
    row = row_at(st)
    assert key(st, "down", "enter")[1] == [("mix_cmd", ["wpctl", "set-mute", str(row.id), "toggle"])]
    assert key(st, "down", "down", "enter")[1] == [("mix_test", row.name)]
    assert key(st, "down", "down", "down", "enter")[1] == [("dev_rename", row.name, row.label)]
    st2, actions = key(st, "down", "down", "down", "down", "enter")
    assert actions == [] and st2.tab == "devices" and st2.dev_focus == ("card", row.card_id)
    st2, actions = key(st, "up", "enter")  # clamps on the first item
    assert actions[0][1][1] == "set-default"
    st2, actions = key(st, *["down"] * 9, "enter")  # Back
    assert actions == [] and st2.mix_menu is None


def test_any_other_key_closes_the_menu_without_acting():
    for k in ("esc", "left", "right", " ", "v"):
        st, _ = key(at("GCS-8 CH 3-AUX"), "enter")
        st, actions = key(st, k)
        assert actions == [] and st.mix_menu is None, k


def test_a_jack_client_with_nothing_to_do_opens_no_menu():
    m = mx(with_reaper(DUMP))
    st, actions = key(at("REAPER", m=m), "enter", m=m)
    assert actions == [] and st.mix_menu is None and st.message == st.failed


def test_menu_follows_its_row_not_a_stale_index():
    st, _ = key(at("GCS-8 CH 3-AUX"), "enter")
    st = dataclasses.replace(st, mix_cursor=0)  # the list shifted under the open menu
    assert labels(st)[0] == "Set as system default" and key(st, "enter")[1][0][1][-1] == str(
        next(r.id for _, _, r in pc.mixer_selectable(MX) if r.label == "GCS-8 CH 3-AUX"))


def test_card_profile_lands_on_the_card_with_its_menu_open():
    inv = pi.inventory(DUMP)
    devices = pc.devices_view(inv, pcfg.default_config())
    st, _ = key(at("GCS-8 CH 3-AUX"), "enter", "down", "down", "down", "down", "enter")
    st = pc.focus_device(st, devices)
    kind, obj = pc.dev_selectable(devices, st)[st.dev_cursor]
    assert kind == "card" and obj.label == "GCS-8" and st.dev_menu == ("card", obj.id) and st.dev_focus is None


def test_the_menu_box_sits_under_its_row_and_stays_in_view():
    st, _ = key(at("GCS-8 CH 3-AUX"), "enter")
    lines, hits = screen(st)
    text = [t for t, _ in lines]
    row = next(i for i, t in enumerate(text) if "GCS-8 CH 3-AUX" in t and "█" in t)
    assert "GCS-8 CH 3-AUX" in text[row + 1] and "> Set as system default" in text[row + 2]
    assert lines[row][1][1][2][2] in ("anchor_src", "anchor_dst")  # [2]: after the box frame's edges  # the row under an open menu is an anchor, not a cursor
    st, _ = key(at("GCS-8 CH 4", "recording"), "enter")
    lines, _ = screen(st, height=20)
    assert any("Back" in t for t, _ in lines)


def test_click_on_a_menu_item_picks_it():
    st, _ = key(at("GCS-8 CH 3-AUX"), "enter")
    _, hits = screen(st)
    row, c0, _ = next((r, a, b) for r, a, b, t in hits if t == ("mix_menu_item", 1))
    _, actions = pc.handle_mouse(st, hits, row, c0, "click", CUR, mixer=MX)
    assert actions[0][1][1] == "set-mute"


def test_legend_leads_with_enter_actions_and_the_menu_has_its_own():
    from test_legends import chips
    assert chips(screen(pc.UIState())[0])[0] == ("Space", "mute")   # Enter's actions are in the status note
    assert "Enter: " in screen(pc.UIState())[0][-1][0]
    st, _ = key(at("GCS-8 CH 3-AUX"), "enter")
    assert chips(screen(st)[0]) == [("Esc", "back")]


def with_volume(dump, node_id, level):
    """The dump with one node's volume set (level on wpctl's scale, 1.0 = 100%)."""
    dump = json.loads(json.dumps(dump))
    node = next(o for o in dump if o["id"] == node_id)
    for props in node["info"]["params"]["Props"]:
        if "channelVolumes" in props:
            props["channelVolumes"] = [level ** 3] * len(props["channelVolumes"])
    return dump


def discord_at(level):
    play = next(r for r in pi.streams(DUMP) if r.direction == "out" and r.label == "Discord")
    m = mx(with_volume(DUMP, play.id, level))
    return at("Discord", m=m), m, play.id


def boosted(level, saved=()):
    play = next(r for r in pi.streams(DUMP) if r.direction == "out" and r.label == "Discord")
    m = mx(with_volume(DUMP, play.id, level), {**pcfg.default_config(), "boost": list(saved)})
    return at("Discord", m=m), m, play


def up(nid, limit):
    return [("mix_cmd", ["wpctl", "set-volume", "--limit", limit, str(nid), "5%+"])]


def test_volume_stops_at_100_percent_and_asks_in_a_popover():
    # Skrappjaw, 2026-10-26: "vol past 100 is ok btw. just needs a warning/unlock." Then: a popover with
    # Don't ask again, and arrows + Enter only.
    st, m, play = boosted(1.0)
    st, actions = key(st, "right", m=m)
    assert actions == [] and st.popup[0] == "vol_boost"
    title, text, buttons = pc.popup_view(st)
    assert "Discord" in text and "100%" in text
    assert [b for b, _ in buttons] == ["Go louder", "Don't ask again", "Cancel"] and pc.popup_cursor(st) == 2


def test_go_louder_unlocks_for_this_session():
    st, m, play = boosted(1.0)
    st, actions = key(st, "right", "left", "left", "enter", m=m)
    assert actions == up(play.id, "1.5") and st.popup is None
    assert key(st, "right", m=m)[1] == up(play.id, "1.5")      # no second popover


def test_dont_ask_again_saves_it():
    st, m, play = boosted(1.0)
    st, actions = key(st, "right", "left", "enter", m=m)
    assert actions == up(play.id, "1.5") + [("boost", play.name, True, "Discord")]


def test_cancel_changes_nothing():
    st, m, play = boosted(1.0)
    st, actions = key(st, "right", "enter", m=m)
    assert actions == [] and st.popup is None and play.id not in st.vol_unlocked


def test_a_saved_boost_never_asks_and_never_stops_at_100():
    st, m, play = boosted(1.0, saved=[play_name := next(r.name for r in pi.streams(DUMP) if r.label == "Discord")])
    assert key(st, "right", m=m)[1] == up(play.id, "1.5")
    st, m, play = boosted(0.5, saved=[play_name])
    assert key(st, "right", m=m)[1] == up(play.id, "1.5")


def test_back_under_100_locks_a_session_unlock_again():
    st, m, play = boosted(1.0)
    st, _ = key(st, "right", "left", "left", "enter", m=m)
    _, m_low, _ = boosted(0.9)                       # the refresh after stepping down
    assert key(st, "right", m=m_low)[1] == up(play.id, "1")
    st, _ = key(st, "right", m=m_low)
    st, actions = key(st, "right", m=m)              # at 100% again: asks again
    assert actions == [] and st.popup[0] == "vol_boost"


def test_a_row_already_past_100_is_not_trapped():
    st, m, play = boosted(1.2)
    assert key(st, "right", m=m)[1] == up(play.id, "1.5")
    assert key(st, "left", m=m)[1] == [("mix_cmd", ["wpctl", "set-volume", "--limit", "1.5", str(play.id), "5%-"])]


def test_the_channel_menu_locks_and_unlocks_boost():
    st, m, play = boosted(0.5)
    st, _ = key(st, "enter", m=m)
    assert labels(st, m) == ["Mute", "Volume boost: locked", "Back"]
    st2, actions = key(st, "down", "enter", m=m)
    assert actions == [("boost", play.name, True, "Discord")]
    st, m, play = boosted(0.5, saved=[play.name])
    st, _ = key(st, "enter", m=m)
    assert labels(st, m)[1] == "Volume boost: unlocked"
    st = dataclasses.replace(st, vol_unlocked=frozenset({play.id}))
    st, actions = key(st, "down", "enter", m=m)
    assert actions == [("boost", play.name, False, "Discord")] and play.id not in st.vol_unlocked


def test_devices_get_the_boost_row_before_back():
    st, _ = key(at("GCS-8 CH 3-AUX"), "enter")
    assert labels(st)[-2:] == ["Volume boost: locked", "Back"]


def test_no_volume_control_is_refused():
    m = mx(with_reaper(DUMP))
    st, actions = key(at("REAPER", m=m), "right", m=m)
    assert actions == [] and "no volume control" in st.message and st.message == st.failed


def test_wheel_scrolls_and_never_changes_volume():
    # Skrappjaw, 2026-10-24: on a long list the wheel scrolls; volume on the wheel clashed with that.
    st = pc.UIState(mix_cursor=3)
    _, hits = screen(st)
    row, c0, _, _ = next(h for h in hits if h[3] == ("mix_row", 0))  # the mouse over a different row
    st2, actions = pc.handle_mouse(st, hits, row, c0, "wheel_down", CUR, mixer=MX)
    assert actions == [] and st2.mix_cursor == 4                     # moved from the selection, not the mouse
    st2, actions = pc.handle_mouse(st2, hits, row, c0, "wheel_up", CUR, mixer=MX)
    assert actions == [] and st2.mix_cursor == 3


def test_double_click_opens_the_menu():
    st = pc.UIState()
    _, hits = screen(st)
    row, c0, _, _ = next(h for h in hits if h[3] == ("mix_row", 0))
    st2, actions = pc.handle_mouse(st, hits, row, c0, "double", CUR, mixer=MX)
    assert actions == [] and st2.mix_menu is not None
    st3, _ = pc.handle_mouse(st2, hits, row, c0, "click", CUR, mixer=MX)
    assert st3.mix_menu is None  # a click on a row closes the menu


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


def test_meter_cells_color_by_level():
    text, spans = pc._meter_cells(1.0)
    assert text == "█" * 16 and {"meter_low", "meter_mid", "meter_high"} <= {r for *_, r in spans}
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
    assert row.rstrip(" │").endswith("·") and "█" in row.split("%", 1)[1]
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



# --- peak hold ---------------------------------------------------------------------------------

def test_peak_marker_hangs_then_falls_20_db_a_second():
    assert pc.held_peak(0.5, 10.0, 11.0, 1.5) == 0.5                      # still in its hold
    assert abs(pc.held_peak(1.0, 10.0, 12.5, 1.5) - 0.1) < 1e-9           # a second past the hold: -20 dB


def test_a_new_peak_restarts_the_hold_and_a_lower_one_does_not():
    assert pc.hold_peak(0.5, 10.0, 0.8, 11.0, 1.5) == (0.8, 11.0)
    assert pc.hold_peak(0.5, 10.0, 0.3, 11.0, 1.5) == (0.5, 10.0)
    assert pc.hold_peak(0.5, 10.0, 0.3, 20.0, 1.5) == (0.3, 20.0)         # the marker has fallen below it


def test_marker_sits_above_the_bar_in_its_zone_color():
    text, spans = pc._meter_cells(10 ** (-30 / 20), hold=10 ** (-3 / 20))  # bar at -30 dB, peak at -3 dB
    m = text.index("▌")
    assert m == 14 and text.count("█") == 6 and (m, m + 1, "meter_high") in spans
    text, _ = pc._meter_cells(10 ** (-3 / 20), hold=10 ** (-30 / 20))      # marker under the bar: not drawn
    assert "▌" not in text
    assert "▌" not in pc._meter_cells(0.5)[0]                               # hold off
