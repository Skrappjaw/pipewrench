"""The now-playing bar: MPRIS players read through busctl's JSON, and its keys on every tab."""
import json
from pathlib import Path

import pytest

import pwcore as pc
import pwinventory as pi
import pwmedia as pm

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
NAME = "org.mpris.MediaPlayer2.brave.instance366124"


def getall(status="Playing", position=50898601, length=258899773, title="Morph", artist=("Twenty One Pilots",)):
    """GetAll as Brave answered it on desktop (trimmed)."""
    meta = {"mpris:length": {"type": "x", "data": length}, "xesam:title": {"type": "s", "data": title},
            "xesam:artist": {"type": "as", "data": list(artist)}, "xesam:album": {"type": "s", "data": "Trench"}}
    return json.dumps({"type": "a{sv}", "data": [{
        "CanControl": {"type": "b", "data": True}, "PlaybackStatus": {"type": "s", "data": status},
        "Position": {"type": "x", "data": position}, "Volume": {"type": "d", "data": 1.0},
        "Metadata": {"type": "a{sv}", "data": meta}}]})


def player(**kw):
    return pm.parse_player(NAME, getall(**kw))


def screen(st, media, width=100, height=30):
    return pc.render_screen(st, CUR, None, [], width, height, media=media)


def bar(lines):
    """The bar is the line just above the key box."""
    top = next(i for i, (t, _) in enumerate(lines) if t.startswith("┌─ Keys"))
    return lines[top - 1]


# --- model --------------------------------------------------------------------------------

def test_names_are_only_mpris_players():
    text = json.dumps({"type": "as", "data": [[":1.5", "org.freedesktop.Notifications", NAME, "org.mpris.MediaPlayer2.vlc"]]})
    assert pm.parse_names(text) == [NAME, "org.mpris.MediaPlayer2.vlc"]
    assert pm.parse_names("") == [] and pm.parse_names("not json") == []


def test_player_from_getall():
    p = player()
    assert (p.label, p.status, p.title, p.artist) == ("Brave", "Playing", "Morph", "Twenty One Pilots")
    assert p.position == 50898601 and p.length == 258899773
    assert pm.parse_player(NAME, "") is None


def test_pick_prefers_playing_then_the_last_shown():
    a, b = player(status="Paused"), pm.Player("org.mpris.MediaPlayer2.vlc", "Vlc", "Playing")
    assert pm.pick([a, b]) == b
    assert pm.pick([a, pm.Player("x", "X", "Stopped")], last=NAME) == a
    assert pm.pick([]) is None


def test_clock():
    assert pm.clock(50898601) == "0:50" and pm.clock(258899773) == "4:18" and pm.clock(3_725_000_000) == "1:02:05"


def test_controls_and_pid_commands():
    assert pm.control_cmd(NAME, "play/pause")[-1] == "PlayPause"
    assert pm.control_cmd(NAME, "stop")[-1] == "Stop" and pm.control_cmd(NAME, "next")[-1] == "Next"
    assert pm.parse_pid(json.dumps({"type": "u", "data": [366124]})) == 366124


def test_stream_found_through_a_helper_process():
    """Brave plays from its audio service, a child of the pid MPRIS gives."""
    streams = [pi.Row(id=207, name="Brave", label="Brave", direction="out", kind="app", pid=366466, volume=1.0),
               pi.Row(id=189, name="Discord", label="Discord", direction="out", kind="app", pid=5000, volume=1.0)]
    parents = {366466: 366124, 366124: 1, 5000: 1}
    assert pm.stream_for(366124, streams, parents.get).id == 207
    assert pm.stream_for(999, streams, parents.get) is None and pm.stream_for(None, streams, parents.get) is None


# --- keys ---------------------------------------------------------------------------------

@pytest.mark.parametrize("tab", pc.TABS)
def test_media_keys_work_on_every_tab(tab):
    for k, action in (("*", ("media", "play/pause")), ("/", ("media", "stop")), ("<", ("media", "previous")),
                      (">", ("media", "next")), ("-", ("media_volume", "-5%")), ("+", ("media_volume", "+5%")),
                      ("=", ("media_volume", "+5%"))):
        assert pc.handle_key(pc.UIState(tab=tab), k, CUR, [])[1] == [action], (tab, k)


# --- the bar --------------------------------------------------------------------------------

@pytest.mark.parametrize("tab", pc.TABS)
def test_bar_sits_above_the_keys_on_every_tab(tab):
    text, _ = bar(screen(pc.UIState(tab=tab), {"player": player(), "position": 50898601}, width=140)[0])
    assert "▶ Brave · Morph — Twenty One Pilots" in text and "0:50 / 4:18" in text
    narrow, _ = bar(screen(pc.UIState(tab=tab), {"player": player(), "position": 50898601}, width=100)[0])
    assert "Brave · Morph" in narrow and "0:50 / 4:18" in narrow  # the artist is cut, not the title or time
    assert " / " in text and " * " in text and " -  + " in text
    assert text.index(" / ") < text.index(" * ") < text.index(" -  + ") < text.index(" < ")  # numpad order


def test_nothing_playing_keeps_the_bar():
    text, style = bar(screen(pc.UIState(), {"player": None})[0])
    assert "nothing playing" in text and "play" in text
    assert bar(screen(pc.UIState(), None)[0])[0] == text


def test_paused_shows_the_pause_glyph():
    text, _ = bar(screen(pc.UIState(), {"player": player(status="Paused"), "position": 0})[0])
    assert "❚❚ Brave" in text


def test_bar_chips_are_clickable():
    st = pc.UIState()
    lines, hits = screen(st, {"player": player(), "position": 0})
    row, c0, c1, _ = next(h for h in hits if h[3] == ("key", "*"))
    assert lines[row][0][c0:c1] == " * "
    assert pc.handle_mouse(st, hits, row, c0, "click", CUR, [])[1] == [("media", "play/pause")]


@pytest.mark.parametrize("width", [40, 80, 160])
def test_bar_fits_any_width(width):
    lines, hits = screen(pc.UIState(), {"player": player(title="x" * 200), "position": 0}, width=width)
    text, _ = bar(lines)
    assert len(text) <= width and " / " in text and "stop" in text
    assert all(c1 <= width for _, _, c1, _ in hits)


def test_dot_is_no_longer_a_media_key():
    assert pc.handle_key(pc.UIState(tab="settings"), ".", CUR, [])[1] == []
