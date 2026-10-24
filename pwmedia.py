"""Pure logic for the now-playing bar: media players over MPRIS (D-Bus), through busctl's JSON output.
No curses and no subprocess calls here; callers pass a runner. Plan: docs/media-bar-plan.md.
"""
import json
from dataclasses import dataclass

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
_BUS = ["busctl", "--user", "--json=short", "call"]
LIST_CMD = _BUS + ["org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames"]
METHODS = {"play/pause": "PlayPause", "previous": "Previous", "next": "Next"}  # no Stop (see pwcore.MEDIA_KEYS)


@dataclass(frozen=True)
class Player:
    name: str                 # its bus name, e.g. org.mpris.MediaPlayer2.brave.instance366124
    label: str                # Brave
    status: str               # Playing | Paused | Stopped
    title: str = ""
    artist: str = ""
    position: int = 0         # microseconds, when it was read
    length: int = 0           # microseconds; 0 when the player doesn't say


def props_cmd(name):
    return _BUS + [name, PATH, "org.freedesktop.DBus.Properties", "GetAll", "s", PLAYER]


def pid_cmd(name):
    return _BUS + ["org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                   "GetConnectionUnixProcessID", "s", name]


def control_cmd(name, op):
    """op: play/pause, previous or next."""
    return ["busctl", "--user", "call", name, PATH, PLAYER, METHODS[op]]


def _data(text):
    try:
        return json.loads(text)["data"]
    except (ValueError, KeyError, TypeError):
        return None


def parse_names(text):
    data = _data(text)
    names = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else []
    return sorted(n for n in names if isinstance(n, str) and n.startswith(PREFIX))


def _label(name):
    """org.mpris.MediaPlayer2.brave.instance366124 → Brave."""
    return name[len(PREFIX):].split(".")[0].replace("_", " ").title() or name


def parse_player(name, text):
    """A Player from GetAll's JSON, or None when the reply isn't one."""
    data = _data(text)
    props = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
    if props is None:
        return None
    get = lambda d, k, default: (d.get(k) or {}).get("data", default) if isinstance(d.get(k), dict) else default  # noqa: E731
    meta = get(props, "Metadata", {}) or {}
    artist = get(meta, "xesam:artist", [])
    return Player(name=name, label=_label(name), status=get(props, "PlaybackStatus", "Stopped"),
                  title=str(get(meta, "xesam:title", "")),
                  artist=", ".join(artist) if isinstance(artist, list) else str(artist),
                  position=int(get(props, "Position", 0) or 0), length=int(get(meta, "mpris:length", 0) or 0))


def parse_pid(text):
    data = _data(text)
    return data[0] if isinstance(data, list) and data and isinstance(data[0], int) else None


def pick(players, last=None):
    """The player the bar shows: one that's playing, else the one shown last, else the first."""
    playing = [p for p in players if p.status == "Playing"]
    if playing:
        return next((p for p in playing if p.name == last), playing[0])
    return next((p for p in players if p.name == last), players[0] if players else None)


def clock(us):
    s = max(0, int(us // 1_000_000))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def stream_for(player_pid, streams, parent_of, depth=6):
    """The playback stream a player owns. Browsers play from a helper process (Brave's audio service is
    a child of the pid MPRIS reports), so a stream matches when the player's pid is it or an ancestor."""
    if not player_pid:
        return None
    for s in streams:
        pid = s.pid
        for _ in range(depth):
            if pid == player_pid:
                return s
            pid = parent_of(pid) if pid else None
    return None
