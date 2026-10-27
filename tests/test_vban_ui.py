"""Settings → Network (VBAN): Skrappjaw's layout, 2026-10-26 (notes/vban-mockups.md rev 2)."""
import dataclasses

import pwcore as pc
import pwpatch as pp
import pwvban as pv

P = pp.Port
GCS8 = pp.Endpoint(30, False, "GCS-8 CH USB", "alsa_output.gcs8", "device",
                   (P(7, 30, "playback_FL", "FL", "input", False), P(8, 30, "playback_FR", "FR", "input", False)))
BRAVE = pp.Endpoint(10, False, "Brave", "Brave", "app", (P(1, 10, "output_FL", "FL", "output", False),
                                                        P(2, 10, "output_FR", "FR", "output", False)))
REAPER = pp.Endpoint(50, False, "REAPER", "REAPER MIDI in", "app", (P(11, 50, "in1", "1", "input", False),), "midi")


def view(incoming=(), outgoing=(), units=None, rules=False):
    c = pv.default_config()
    for e in incoming:
        c = pv.add_entry(c, "in", e, rules=rules)
    for e in outgoing:
        c = pv.add_entry(c, "out", e, rules=rules)
    return pv.View(c, units or {}, "192.168.1.99", "desktop", (1, 0, 5), rules, pp.Graph([BRAVE], [GCS8, REAPER], {}))


def envy_in(**kw):
    return {**pv.new_entry("in", "Studio", "192.168.1.86"), "target": "alsa_output.gcs8", **kw}


def envy_out(**kw):
    return {**pv.new_entry("out", "Studio", "192.168.1.86"), "target": "Brave", **kw}


def state(v=None, cursor=0, **kw):
    return pc.UIState(tab="settings", settings_cat="network", settings_focus="pane", net=v, cursor=cursor, **kw)


def key(st, *keys):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, {})
        actions += a
    return st, actions


def screen(st, width=110, height=40):
    return pc.render_screen(st, {}, None, width, height)


def text(st, **kw):
    return "\n".join(t for t, _ in screen(st, **kw)[0])


def status_line(st):
    return screen(st)[0][-1][0].strip()


def row_of(st, rid):
    return pc.net_rows(st.net).index(rid)


# --- layout ------------------------------------------------------------------------

def test_network_sits_between_audio_and_preferences():
    assert [c for c, _ in pc.SETTINGS_CATS] == ["audio", "network", "prefs"]


def test_rows_this_machine_then_incoming_then_outgoing():
    v = view([envy_in()], [envy_out()])
    assert pc.net_rows(v) == (("addr",), ("pw",), ("in", "studio"), ("in_add",), ("out", "studio"), ("out_add",))
    assert pc.net_rows(None) == (("addr",), ("pw",), ("in_add",), ("out_add",))


def test_page_has_three_boxes_in_order():
    t = text(state(view([envy_in()], [envy_out()])))
    a, b, c = t.index("This machine"), t.index("Incoming streams"), t.index("Outgoing streams")
    assert a < b < c
    assert "192.168.1.99 · desktop" in t and "1.0.5 · one incoming stream per port" in t
    assert t.count("+ Add stream…") == 2


def test_stream_rows_show_ip_port_target_and_format():
    t = text(state(view([envy_in()], [envy_out()], units={"in-studio": "active", "out-studio": "active"})))
    assert "● Studio" in t and "192.168.1.86" in t and ":6980" in t
    assert "→ GCS-8 CH USB" in t and "← Brave" in t and "48k · 2ch" in t


def test_status_words_off_and_failed():
    t = text(state(view([envy_in(on=False)], [envy_out()], units={"out-studio": "failed"})))
    assert "○ Studio" in t and "off" in t and "failed" in t


def test_not_connected_and_gone_targets():
    t = text(state(view([envy_in(target=None)], [envy_out(target="Discord")])))
    assert "→ not connected" in t and "← Discord (not here)" in t


def test_rules_mode_says_so():
    assert "senders matched by IP" in text(state(view(rules=True)))


def test_reading_before_the_first_refresh():
    assert "reading…" in text(state(None))


def test_cursor_bar_on_the_selected_row():
    st = state(view([envy_in()], units={"in-studio": "active"}), cursor=2)
    lines, _ = screen(st)
    line = next((t, s) for t, s in lines if "> ● Studio" in t)
    assert pc._has_cursor(line[1])


# --- keys --------------------------------------------------------------------------

def test_arrows_walk_every_row_and_left_goes_back():
    st = state(view([envy_in()], [envy_out()]))
    st, _ = key(st, *["down"] * 10)
    assert st.cursor == 5
    st, _ = key(st, "left")
    assert st.settings_focus == "menu"


def test_enter_on_add_opens_the_add_popover():
    st = state(view())
    st, _ = key(st, "down", "down", "enter")  # ("in_add",)
    assert st.popup == ("vban_add", "in")
    title, body, buttons = pc.popup_view(st)
    assert title == "Add incoming stream" and [b for b, _ in buttons] == ["Detect on network", "Manual", "Cancel"]
    st2, actions = key(st, "left", "enter")  # Cancel is the default: one left is Manual
    assert actions == [("vban_manual", "in")] and st2.popup is None
    _, actions = key(st, "left", "left", "enter")
    assert actions == [("vban_detect", "in")]


def test_outgoing_add_popover():
    st = state(view())
    st, _ = key(st, *["down"] * 3, "enter")
    assert st.popup == ("vban_add", "out") and pc.popup_view(st)[0] == "Add outgoing stream"


def test_esc_cancels_the_add_popover():
    st, actions = key(state(view()), "down", "down", "enter", "esc")
    assert st.popup is None and actions == [] and st.message == "cancelled"


def test_enter_on_a_stream_opens_its_menu():
    st = state(view([envy_in()]), cursor=2)
    st, _ = key(st, "enter")
    purpose, d, eid, title, items = st.pick
    assert (purpose, d, eid, title) == ("menu", "in", "studio", "Studio")
    assert [label for label, _ in items] == ["Turn off", "Playback destination…", "Format…", "Rename…", "Remove"]


def test_outgoing_menu_says_source_audio():
    st = state(view([], [envy_out(on=False)]), cursor=3)
    st, _ = key(st, "enter")
    assert [label for label, _ in st.pick[4]][:2] == ["Turn on", "Source audio…"]


def test_menu_toggle():
    st = state(view([envy_in()]), cursor=2)
    st, actions = key(st, "enter", "enter")
    assert actions == [("vban_set", "in", "studio", {"on": False})] and st.pick is None


def test_menu_destination_opens_select_playback_destination_on_the_current_one():
    st = state(view([envy_in()]), cursor=2)
    st, _ = key(st, "enter", "down", "enter")
    purpose, d, eid, title, items = st.pick
    assert (purpose, title) == ("target", "Select Playback Destination")
    assert items == (("Not connected", None), ("GCS-8 CH USB", "alsa_output.gcs8"))  # audio only; REAPER is MIDI
    assert st.pick_cursor == 1
    st, actions = key(st, "up", "enter")
    assert actions == [("vban_set", "in", "studio", {"target": None})]


def test_select_source_audio_for_outgoing():
    st = state(view([], [envy_out()]), cursor=3)
    st, _ = key(st, "enter", "down", "enter")
    assert st.pick[3] == "Select Source Audio" and st.pick[4] == (("Not connected", None), ("Brave", "Brave"))


def test_midi_stream_picks_midi_destinations():
    v = view([envy_in(media="midi", target=None)])
    p = pc.target_pick(v, "in", pc.net_entry(v, "in", "studio"))
    assert p[4] == (("Not connected", None), ("REAPER", "REAPER MIDI in"))


def test_menu_format():
    st = state(view([envy_in()]), cursor=2)
    st, _ = key(st, "enter", "down", "down", "enter")
    assert st.pick[0] == "format" and st.pick_cursor == 0  # 48 kHz · 16-bit · stereo
    assert [label for label, _ in st.pick[4]][-1] == "MIDI"
    st, actions = key(st, *["down"] * 9, "enter")
    assert actions == [("vban_format", "in", "studio", "midi")]


def test_menu_remove_asks_first():
    st = state(view([envy_in()]), cursor=2)
    st, actions = key(st, "enter", "down", "down", "down", "down", "enter")
    assert st.popup == ("vban_remove", "in", "studio", "Studio") and actions == []
    assert pc.popup_view(st)[1] == "Remove Studio? Its stream stops now."
    st, actions = key(st, "left", "enter")
    assert actions == [("vban_remove", "in", "studio", "Studio")]


def test_pick_esc_cancels():
    st, actions = key(state(view([envy_in()]), cursor=2), "enter", "esc")
    assert st.pick is None and actions == [] and st.message == "cancelled"


def test_a_stream_that_went_away_mid_pick():
    st = state(view([envy_in()]), cursor=2)
    st, _ = key(st, "enter")
    st = dataclasses.replace(st, net=view())
    st, actions = key(st, "enter")
    assert actions == [] and st.message == "that stream has gone"


def test_the_pick_holds_the_keys_like_a_popover():
    st, _ = key(state(view([envy_in()]), cursor=2), "enter")
    st2, _ = key(st, "tab")
    assert st2.pick and st2.tab == "settings"  # Tab doesn't slip past it
    st3, _ = pc._leave(dataclasses.replace(st))
    assert st3.pick is None


def test_with_net_keeps_the_cursor_on_its_row():
    st = state(view([envy_in()], [envy_out()]), cursor=4)  # ("out", "studio")
    st = pc.with_net(st, view([envy_in(), {**envy_in(), "name": "Desk", "id": "desk", "ip": "10.0.0.2"}], [envy_out()]))
    assert pc.net_rows(st.net)[st.cursor] == ("out", "studio")


# --- notes, legend, popover drawing, mouse ------------------------------------------

def test_notes_explain_each_row():
    v = view([envy_in()], [envy_out()])
    assert status_line(state(v, 0)).startswith("what other machines send to")
    assert "VBAN is built into PipeWire" in status_line(state(v, 1))
    assert status_line(state(v, 2)) == ("From Studio (sends to 192.168.1.99:6980) to GCS-8 CH USB"
                                        " · Enter: on/off, destination, format, rename, remove")
    assert "Enter: detect on network, manual" in status_line(state(v, 3))
    assert status_line(state(v, 4)) == ("To Studio (192.168.1.86:6980), fed from Brave"
                                        " · Enter: on/off, source, format, rename, remove")
    assert status_line(pc.UIState(tab="settings", settings_cat="network")).startswith("VBAN: audio and MIDI")


def test_pick_note_and_legend():
    st, _ = key(state(view([envy_in()]), cursor=2), "enter")
    assert status_line(st) == pc.PICK_NOTE
    assert pc.legend(st) == [("Esc", "cancel")]
    assert pc.legend(state(view())) == pc.LEGENDS["settings"]


def test_pick_box_is_drawn_with_its_title_and_marked_item():
    st, _ = key(state(view([envy_in()]), cursor=2), "enter", "down", "enter")
    t = text(st)
    assert "Select Playback Destination" in t and "> GCS-8 CH USB" in t and "  Not connected" in t


def test_pick_click_picks():
    st, _ = key(state(view([envy_in()]), cursor=2), "enter", "down", "enter")
    lines, hits = screen(st)
    row, c0, _, target = next(h for h in hits if h[3] == ("pick_item", 0))
    st2, actions = pc.handle_mouse(st, hits, row, c0 + 1, "click", {})
    assert actions == [("vban_set", "in", "studio", {"target": None})]
    assert all(h[3][0] == "pick_item" for h in hits)  # nothing else takes clicks while it's up


def test_pick_wheel_moves():
    st, _ = key(state(view([envy_in()]), cursor=2), "enter")
    _, hits = screen(st)
    st2, _ = pc.handle_mouse(st, hits, 0, 0, "wheel_down", {})
    assert st2.pick_cursor == 1


def test_tap_a_stream_row_then_tap_again_opens_its_menu():
    st = state(view([envy_in()]), cursor=0)
    _, hits = screen(st)
    row = next(r for r, c0, c1, t in hits if t == ("setting", 2))
    st, _ = pc.handle_mouse(st, hits, row, 20, "click", {})
    assert st.cursor == 2 and st.pick is None
    _, hits = screen(st)
    st, _ = pc.handle_mouse(st, hits, row, 20, "click", {})
    assert st.pick and st.pick[0] == "menu"


def test_narrow_screen_still_draws():
    for w in (60, 80):
        assert "Incoming streams" in text(state(view([envy_in()], [envy_out()])), width=w)


def test_menu_rename():
    st = state(view([envy_in()]), cursor=2)
    st, actions = key(st, "enter", "down", "down", "down", "enter")
    assert actions == [("vban_rename", "in", "studio")] and st.pick is None


def test_outgoing_detect_searches_the_network():
    st = state(view())
    st, _ = key(st, *["down"] * 3, "enter")
    assert pc.popup_view(st)[1] == "Search the network for computers to send to, or type one in."
    _, actions = key(st, "left", "left", "enter")
    assert actions == [("vban_detect", "out")]


def test_found_pick_hands_back_the_machine():
    f = pv.Found("192.168.1.86", "studio-pc", True)
    st = pc.open_pick(state(view()), pc.found_pick([f]))
    assert st.pick[3] == "Machines on the network" and st.pick[4][0][0] == "studio-pc · 192.168.1.86 · sends VBAN"
    _, actions = key(st, "enter")
    assert actions == [("vban_found", "out", f)]


def test_incoming_detect_lists_heard_streams_then_found_machines():
    h = pv.Heard("studio-pc", "audio", 48000, 2, "S16LE")
    f = pv.Found("192.168.1.23", "den-pc", False)
    st = pc.open_pick(state(view()), pc.detect_pick([("192.168.1.86", h)], [f]))
    assert [label for label, _ in st.pick[4]] == ["studio-pc · 192.168.1.86 · 48 kHz · 2 ch", "den-pc · 192.168.1.23"]
    _, actions = key(st, "enter")
    assert actions == [("vban_heard", "in", ("192.168.1.86", h))]
    _, actions = key(st, "down", "enter")
    assert actions == [("vban_found", "in", f)]
    assert pc.popup_view(pc.UIState(popup=("vban_add", "in")))[1] == "Listen for VBAN senders and search the network, or type one in."
