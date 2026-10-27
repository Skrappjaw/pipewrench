"""Pure logic for PipeWrench: PipeWire settings metadata, pw-top parsing, and the TUI
state machine. No curses and no subprocess calls here; callers pass a runner.

Behaviour follows PipeControl (github.com/portaloffreedom/pipecontrol v0.3.0):
settings keys from src/pw/qpipewiresettings.cpp, choice lists from resources/Settings.qml.
"""
import dataclasses
import json
import math
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pwinventory as pi
import pwkeys as pk
import pwrates
import pwmedia as pm
import pwpatch as pp
import pwrealtime as prt
import pwthemes as pt

READ_CMD = ["pw-metadata", "-n", "settings", "0"]
RESTART_CMD = ["systemctl", "--user", "restart", "wireplumber"]

# Colour roles as (fg, bg, attr) per theme, loaded from TOML: built-ins in themes/, the user's in
# ~/.config/pipewrench/themes/ (pwthemes; guide docs/themes.md). install_themes refills these names.
THEMES, THEME_NAMES, THEME_LABELS, THEME_MIN_COLOURS, THEME_LOGOS, THEME_USER = {}, [], {}, {}, {}, []


def install_themes(user_dir=None):
    """Load the built-in themes, plus the user's when given; returns the problems found."""
    global THEMES, THEME_NAMES, THEME_LABELS, THEME_MIN_COLOURS, THEME_LOGOS, THEME_USER
    lib = pt.load([pt.BUILTIN_DIR] + ([user_dir] if user_dir else []))
    THEMES, THEME_NAMES, THEME_USER = lib.themes, lib.names, lib.user
    THEME_LABELS = {n: lib.label(n) for n in lib.names}
    THEME_MIN_COLOURS = {n: lib.min_colours(n) for n in lib.names}
    THEME_LOGOS = {n: lib.logo(n) for n in lib.names}
    return lib.problems


install_themes()


def theme_label(name):
    return THEME_LABELS.get(name, name)


def opened_theme_row(before, after):
    """True when this key or click opened Settings → Display → Theme: the moment to re-read theme files."""
    return (after.editing and not before.editing and after.settings_cat == "prefs" and after.cursor == THEME_ROW
            and after.tab == "settings")


def themes_message(problems):
    """(message, failed) for the band after loading themes."""
    if problems:
        more = f" (+{len(problems) - 1} more: pwrench theme check)" if len(problems) > 1 else ""
        return problems[0] + more, problems[0] + more
    mine = len(THEME_USER)
    return f"{len(THEME_NAMES)} themes, {mine} yours" if mine else f"{len(THEME_NAMES)} themes", ""

METER_FALL = [10, 20, 40, 80]  # dB a second: slow, VU-like, through fast
PEAK_HOLD = [0, 0.5, 1, 1.5, 2, 3]  # seconds a peak marker hangs before it falls; 0 is off
PEAK_FALL = 20  # dB a second, once the hold is over
METER_FPS = [5, 10, 15, 20, 30, 45, 60]  # new audio lands once per graph cycle (21 ms at 1024/48k); falls redraw every frame


def hex_rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def nearest_256(colour):
    """The xterm-256 index closest to a #rrggbb colour (the 6×6×6 cube or the grey ramp)."""
    r, g, b = hex_rgb(colour)
    steps = [0, 95, 135, 175, 215, 255]
    near = lambda v: min(range(6), key=lambda i: abs(steps[i] - v))  # noqa: E731
    cube = 16 + 36 * near(r) + 6 * near(g) + near(b)
    grey_i = max(0, min(23, round(((r + g + b) / 3 - 8) / 10)))
    candidates = {cube: tuple(steps[near(v)] for v in (r, g, b)), 232 + grey_i: (8 + 10 * grey_i,) * 3}
    return min(candidates, key=lambda k: sum((x - y) ** 2 for x, y in zip(candidates[k], (r, g, b))))


def ui_path():
    if os.environ.get("PIPEWRENCH_UI"):
        return Path(os.environ["PIPEWRENCH_UI"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "pipewrench" / "ui.json"


def load_ui(path):
    """PipeWrench's own display preferences; a missing or broken file gives the defaults."""
    try:
        ui = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        ui = {}
    theme = ui.get("theme") if isinstance(ui, dict) else None
    theme = {"orange": "crowbar", "portal-light": "pipeos-lite", "portal-dark": "pipeos-dark"}.get(theme, theme)  # renamed
    ui = ui if isinstance(ui, dict) else {}
    return {"theme": theme if theme in THEMES else "dark",
            "meter_fps": ui.get("meter_fps") if ui.get("meter_fps") in METER_FPS else 10,
            "meter_fall": ui.get("meter_fall") if ui.get("meter_fall") in METER_FALL else 20,
            "peak_hold": ui.get("peak_hold") if ui.get("peak_hold") in PEAK_HOLD else 1.5,
            "volume_bar": ui.get("volume_bar") if isinstance(ui.get("volume_bar"), bool) else True,
            "meters": ui.get("meters") if isinstance(ui.get("meters"), bool) else True,
            "key_box": ui.get("key_box") if isinstance(ui.get("key_box"), bool) else True,
            "dev_show_hidden": ui.get("dev_show_hidden") if isinstance(ui.get("dev_show_hidden"), bool) else False,
            "dev_monitors": ui.get("dev_monitors") if isinstance(ui.get("dev_monitors"), bool) else False,
            "keys": pk.clean(ui.get("keys"))}


def ui_prefs(st):
    """What ui.json keeps, from the live state."""
    return {"theme": st.theme, "meter_fps": st.meter_fps, "meter_fall": st.meter_fall, "peak_hold": st.peak_hold,
            "volume_bar": st.volume_bar, "meters": st.meters, "key_box": st.key_box,
            "dev_show_hidden": st.dev_show_hidden, "dev_monitors": st.dev_monitors,
            "keys": pk.overrides(pk.bindings(st.keys))}


def save_ui(path, ui):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ui, indent=2) + "\n")


class MetadataError(Exception):
    pass


# --- settings model ----------------------------------------------------------

@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    short: str
    choices: list


_BUFFERS = [8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]
LOG_NAMES = ["No log", "Error", "Warning", "Info", "Debug", "Trace"]

# In screen order (Skrappjaw, 2026-10-26): the overrides, the limits, then Log level, alone under Advanced.
SETTINGS = [
    Setting("clock.force-rate", "Force sample rate", "force rate",
            [0, 44100, 48000, 88200, 96000, 176400, 192000]),
    Setting("clock.force-quantum", "Force buffer size", "force buffer", [0] + _BUFFERS),
    Setting("clock.min-quantum", "Minimum buffer", "min buffer", _BUFFERS),
    Setting("clock.max-quantum", "Max buffer", "max buffer", _BUFFERS),
    Setting("log.level", "Log level", "log level", list(range(6))),
]
KEYS = [s.key for s in SETTINGS]
FORCE_KEYS = ("clock.force-rate", "clock.force-quantum")
# Settings → Audio → Reset: PipeWire's stock clock (/usr/share/pipewire/pipewire.conf). Log level keeps its value.
CLOCK_DEFAULTS = {"clock.force-rate": 0, "clock.force-quantum": 0, "clock.min-quantum": 32, "clock.max-quantum": 2048}


def setting(key):
    for s in SETTINGS:
        if s.key == key:
            return s
    raise KeyError(key)


def latency_ms(quantum, rate):
    """One buffer's period, quantum ÷ rate, as '2.67 ms'; '' when either is unknown."""
    if not isinstance(quantum, int) or not isinstance(rate, int) or quantum <= 0 or rate <= 0:
        return ""
    ms = quantum / rate * 1000
    return f"{ms:.2f} ms" if ms < 10 else f"{ms:.1f} ms"


def row_latency(s, value, merged):
    """The latency a clock row stands for: a buffer row at the rate in use, the rate row with the buffer in use.
    In use = the forced value (pending counts), else PipeWire's default (clock.rate, clock.quantum)."""
    rate = merged.get("clock.force-rate") or merged.get("clock.rate")
    quantum = merged.get("clock.force-quantum") or merged.get("clock.quantum")
    if s.key == "clock.force-rate":
        return latency_ms(quantum, value or merged.get("clock.rate"))
    if s.key in ("clock.min-quantum", "clock.max-quantum"):
        return latency_ms(value, rate)
    if s.key == "clock.force-quantum":
        return latency_ms(value or merged.get("clock.quantum"), rate)
    return ""


def display_value(s, value):
    if value is None:
        return "?"
    if s.key in FORCE_KEYS and value == 0:
        return "off"
    if s.key == "log.level" and 0 <= value < len(LOG_NAMES):
        return LOG_NAMES[value]
    if value not in s.choices:
        return f"custom:{value}"
    return str(value)


def cycle(s, current, step):
    """Step to the neighbouring choice; clamp at the ends. Off-list values snap toward step."""
    choices = sorted(s.choices)
    if current in choices:
        i = max(0, min(len(choices) - 1, choices.index(current) + step))
        return choices[i]
    if step > 0:
        return next((c for c in choices if c > current), choices[-1])
    return next((c for c in reversed(choices) if c < current), choices[0])


# --- metadata read/write -----------------------------------------------------

_UPDATE = re.compile(r"update: id:(\d+) key:'([^']*)' value:'([^']*)'")


def _value(raw):
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if raw.startswith("["):
        return [int(n) for n in re.findall(r"\d+", raw)]
    return raw


def parse_settings(text):
    if 'Found "settings" metadata' not in text:
        raise MetadataError(text.strip() or "no settings metadata")
    out = {}
    for line in text.splitlines():
        m = _UPDATE.search(line)
        if m and m.group(1) == "0":
            out[m.group(2)] = _value(m.group(3))
    return out


def read_settings(run):
    rc, out = run(READ_CMD)
    if rc != 0:
        raise MetadataError(out.strip() or f"pw-metadata exited {rc}")
    return parse_settings(out)


def _check_value(key, value):
    if key not in KEYS:
        raise ValueError(f"unknown setting {key!r}")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} needs a whole number >= 0, got {value!r}")


def set_cmd(key, value):
    _check_value(key, value)
    return ["pw-metadata", "-n", "settings", "0", key, str(value)]


def validate(current, changes, devices=None):
    """Raise ValueError for impossible combinations; return warnings for ones PipeWire will adjust.
    devices: pwrates.read()'s list, so a forced rate is checked against what each card supports."""
    merged = {**current, **changes}
    lo, hi = merged.get("clock.min-quantum"), merged.get("clock.max-quantum")
    if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
        raise ValueError(f"min buffer {lo} is larger than max buffer {hi}")
    warnings = []
    fq = merged.get("clock.force-quantum")
    if fq and isinstance(lo, int) and isinstance(hi, int) and not lo <= fq <= hi:
        warnings.append(f"force buffer {fq} is outside min/max {lo}-{hi}; PipeWire will clamp it")
    if "clock.force-rate" in changes:
        warnings += pwrates.warnings(merged.get("clock.force-rate"), merged.get("clock.allowed-rates"),
                                     merged.get("clock.rate"), devices)
    return warnings


def ordered_changes(current, changes):
    """Order writes so min <= max holds after every step, and force values land last."""
    for k, v in changes.items():
        _check_value(k, v)
    out = []
    lo, hi = "clock.min-quantum", "clock.max-quantum"
    if lo in changes and hi in changes:
        cur_hi = current.get(hi)
        first = hi if isinstance(cur_hi, int) and changes[lo] > cur_hi else lo
        out += [(first, changes[first]), *[(k, changes[k]) for k in (lo, hi) if k != first]]
    else:
        out += [(k, changes[k]) for k in (lo, hi) if k in changes]
    out += [(k, changes[k]) for k in ("clock.force-rate", "clock.force-quantum", "log.level") if k in changes]
    return out


# --- pw-top parsing ----------------------------------------------------------

@dataclass
class TopRow:
    state: str
    id: int
    quant: int
    rate: int
    wait: str
    busy: str
    wq: str
    bq: str
    err: int
    format: str
    name: str
    follower: bool


class TopParser:
    """Feed `pw-top -b` lines; each header line closes the previous block and returns it."""

    def __init__(self):
        self.cols = None
        self.rows = []

    def feed(self, line):
        if line.startswith("S ") and "QUANT" in line and "NAME" in line:
            done = self.rows if self.cols else None
            self.cols = (line.index("ERR") + 3, line.index("NAME"))
            self.rows = []
            return done
        if not self.cols or not line.strip():
            return None
        err_end, name_col = self.cols
        tok = line[:err_end].split()
        if len(tok) != 9:
            return None
        try:
            rest = line[name_col:]
            follower = rest.startswith(" + ")
            self.rows.append(TopRow(tok[0], int(tok[1]), int(tok[2]), int(tok[3]), tok[4], tok[5],
                                    tok[6], tok[7], int(tok[8]), line[err_end:name_col].strip(),
                                    (rest[3:] if follower else rest).strip(), follower))
        except ValueError:
            pass
        return None

    def flush(self):
        rows, self.rows = self.rows, []
        return rows if self.cols else []


# --- TUI state machine -------------------------------------------------------

# Basic first (everyday audio), then the engineering tools. About is last.
TABS = ["mixer", "devices", "patch", "settings", "top", "about"]
TAB_TITLES = {t: f"{n} {t.capitalize()}" for n, t in enumerate(TABS, start=1)}


@dataclass
class UIState:
    tab: str = "mixer"
    mix_cursor: int = 0
    mix_menu: tuple = None       # (section, node id) of the row whose menu is open
    mix_menu_cursor: int = 0
    vol_unlocked: frozenset = frozenset()  # node ids let past 100% this session (Go louder)
    meters: bool = True          # live levels on the Mixer (v toggles)
    cursor: int = 0              # Settings: the row in the open category
    pending: dict = field(default_factory=dict)
    settings_cat: str = "audio"  # Settings: the category open on the right (SETTINGS_CATS)
    settings_focus: str = "menu" # menu | pane: where Settings' keys go
    clock_button: int = 0        # Settings → Audio: Apply · Save · Reset
    top_scroll: int = 0
    dev_cursor: int = 0
    dev_show_hidden: bool = False
    dev_monitors: bool = False
    dev_menu: tuple = None       # ("row", node name) | ("card", id) | ("pending", bus name)
    dev_menu_cursor: int = 0
    dev_focus: tuple = None      # ("card", id): select that card on Devices with its menu open (Mixer → Card profile…)
    patch_view: str = "cols"     # cols | grid
    patch_side: str = "src"      # src | dst (column view)
    patch_src: int = 0
    patch_dst: int = 0
    patch_monitors: bool = False
    patch_ports: bool = False
    patch_port_row: int = 0      # source port in the port matrix
    patch_port_col: int = 0      # destination port
    patch_menu: bool = False     # the slot menu under the destination
    patch_menu_cursor: int = 0
    popup: tuple = None          # the open popover: a kind tuple (POPUPS), answered with ←→ and Enter
    popup_button: int = None     # its selected button; None is the kind's default (Cancel, or the only one)
    message: str = ""
    failed: str = ""             # the message is drawn red while it still equals this
    pw_down: str = ""            # the Watchdog's banner while PipeWire isn't answering; it replaces the rule
    audio_rows: tuple = ()       # Settings → Audio's row ids (audio_rows) for the data on screen; () = nothing read
    theme: str = "dark"
    meter_fps: int = 10
    meter_fall: int = 20         # dB a second
    peak_hold: float = 1.5       # seconds; 0 is off
    volume_bar: bool = True      # the bar under each Mixer channel; off leaves just the %
    key_box: bool = True         # the key list at the bottom of the screen; off hides it, keys still work (Skrappjaw, 2026-10-26)
    keys: dict = field(default_factory=dict)  # key binding overrides {action id: key or None} (pwkeys)
    key_capture: str = None      # Settings → Keys: the action id waiting for its new key
    editing: bool = False        # Settings: Enter opened the row's value; ←→ change it (Skrappjaw, 2026-10-25)
    edit_from: tuple = None      # what Esc restores: (had pending, old pending) or (pref attr, old value)


# The now-playing bar's keys work on every tab; no tab uses these characters. * - + sit on the numpad.
# No stop: Brave ends its media session on Stop, and only the browser can start it again.
MEDIA_KEYS = {"*": ("media", "play/pause"), "<": ("media", "previous"),
              ">": ("media", "next"), "-": ("media_volume", "-5%"), "+": ("media_volume", "+5%"),
              "=": ("media_volume", "+5%")}


NAV_KEYS = ("up", "down", "left", "right", "tab", "btab", "pgup", "pgdn")


def handle_key(st, key, current, devices=None, patch=None, mixer=None):
    """Return (new_state, actions). Nothing touches PipeWire until an action is executed.

    devices is the Devices tab's view (devices_view), or None until loaded.
    """
    st = dataclasses.replace(st, pending=dict(st.pending))
    if st.key_capture:
        return _capture_key(st, key)
    if st.popup:
        return _key_popup(st, key)
    if key in NAV_KEYS or (key.isdigit() and 1 <= int(key) <= len(TABS)):
        st.message = st.failed = ""  # a message lasts until you move, then the row's note is back (Skrappjaw, 2026-10-26)
    if key == "q":
        st.popup, st.popup_button = ("quit",), None
        return st, []
    if key in ("tab", "btab"):
        st, actions = _leave(st)
        st.tab = TABS[(TABS.index(st.tab) + (1 if key == "tab" else -1)) % len(TABS)]
        return st, actions
    if key.isdigit() and 1 <= int(key) <= len(TABS):
        st, actions = _leave(st)
        st.tab = TABS[int(key) - 1]
        return st, actions
    if key in MEDIA_KEYS:
        return st, [MEDIA_KEYS[key]]
    if st.tab in pk.SCREENS and not (st.dev_menu or st.mix_menu or st.patch_menu):
        key = pk.translate(st.tab, key, pk.bindings(st.keys))
        if key is None:
            return st, []
    if st.tab == "mixer":
        return _key_mixer(st, key, mixer)
    if st.tab == "devices":
        return _key_devices(st, key, devices)
    if st.tab == "patch":
        return _key_patch(st, key, patch)
    if st.tab == "about":
        return st, []
    return {"settings": _key_settings, "top": _key_top}[st.tab](st, key, current)


# Settings is a menu of categories on the left with the open one on the right (Skrappjaw, 2026-10-25).
# Each category is its own list; st.cursor is the row in the open one.
SETTINGS_CATS = [("audio", "Audio"), ("prefs", "Preferences")]
MENU_W = 15                         # the menu column, its │ included


# Audio is a list of rows, every one selectable (Skrappjaw, 2026-10-26: "the navigation breaks with so many things
# being not interactable"), in the order PipeWire is built: the server, the devices it manages, the clock you
# change, then Advanced. Rows are ids, not fixed numbers: how many depends on the cards and Realtime's checks.
def audio_rows(realtime=None, rates=None):
    """The Audio row ids for this data. st.cursor is an index into them."""
    rows = [("server",), ("realtime",)]
    if isinstance(realtime, prt.Status):
        rows += [("rt", label) for label, _, _ in prt.rows(realtime)]
        if not realtime.ok:
            rows += [("fix", n) for n in range(len(prt.fix(realtime)))]
    rows += [("now",), ("buffer_by",), ("rate_by",)]
    rows += [("device", r.label) for r in rates] if rates else [("device", None)]
    rows += [("setting", i) for i in range(len(SETTINGS) - 1)] + [("allowed",), ("defaults",), ("buttons",), ("log",)]
    return tuple(rows)


def _rows(st):
    return st.audio_rows or audio_rows()


def audio_index(rid, rows=None):
    """Where a row id sits (nothing read yet, unless rows are given)."""
    rows = rows or audio_rows()
    return rows.index(rid)


def audio_row(st, cursor=None):
    rows = _rows(st)
    cursor = st.cursor if cursor is None else cursor
    return rows[cursor] if 0 <= cursor < len(rows) else None


def audio_setting(cursor, rows=None):
    """The Setting on an Audio row, or None for a row that only shows something (or the buttons)."""
    rows = rows or audio_rows()
    rid = rows[cursor] if 0 <= cursor < len(rows) else None
    if rid == ("log",):
        return SETTINGS[-1]
    return SETTINGS[rid[1]] if rid and rid[0] == "setting" else None


def with_audio_rows(st, rows):
    """New data on Audio: keep the cursor on the same row while cards and checks come and go."""
    if rows == st.audio_rows:
        return st
    rid = audio_row(st) if st.settings_cat == "audio" else None
    st = dataclasses.replace(st, audio_rows=rows)
    if rid in rows:
        st.cursor = rows.index(rid)
    elif st.settings_cat == "audio":
        st.cursor = max(0, min(len(rows) - 1, st.cursor))
    return st
CLOCK_BUTTONS = ["Apply", "Save", "Reset", "Restart"]
# Preferences: Display (PipeWrench's own rows; they apply at once), then Keys.
THEME_ROW = 0
FPS_ROW = THEME_ROW + 1
FALL_ROW = FPS_ROW + 1
HOLD_ROW = FALL_ROW + 1
BAR_ROW = HOLD_ROW + 1
METERS_ROW = BAR_ROW + 1
SHOW_HIDDEN_ROW = METERS_ROW + 1
MONITORS_ROW = SHOW_HIDDEN_ROW + 1
KEY_BOX_ROW = MONITORS_ROW + 1
KEYS_ROW = KEY_BOX_ROW + 1     # Key Bindings: one row per BINDING_ROWS entry
# Global's fixed keys, then each view's actions in pwkeys order (headings are drawn, never cursor rows).
BINDING_ROWS = [("fixed", f) for f in pk.FIXED] + [("action", a) for a in pk.ACTIONS]
RESET_KEYS_ROW = KEYS_ROW + len(BINDING_ROWS)


PREF_ROWS = {THEME_ROW: "theme", FPS_ROW: "meter_fps", FALL_ROW: "meter_fall", HOLD_ROW: "peak_hold",
             BAR_ROW: "volume_bar", METERS_ROW: "meters",
             SHOW_HIDDEN_ROW: "dev_show_hidden", MONITORS_ROW: "dev_monitors", KEY_BOX_ROW: "key_box"}


def _leave(st):
    """Leaving a view or the Settings pane resets it (Skrappjaw, 2026-10-25): open menus close, an edit is
    cancelled and unapplied clock changes are dropped. List positions and view choices stay."""
    actions = _restore_pref(st) if st.editing and st.settings_cat == "prefs" else []
    dropped = bool(st.pending)
    st.pending, st.editing, st.edit_from, st.key_capture = {}, False, None, None
    st.mix_menu = st.dev_menu = None
    st.patch_menu = st.patch_ports = False
    st.settings_focus, st.cursor = "menu", 0
    if dropped:
        st.message = "unapplied changes dropped"
    return st, actions


def _begin_edit(st):
    """Open the cursor row's value for ←→. Only value rows open; buttons and Keys rows don't."""
    if st.settings_cat == "audio" and audio_setting(st.cursor, st.audio_rows):
        k = audio_setting(st.cursor, st.audio_rows).key
        st.editing, st.edit_from = True, (k in st.pending, st.pending.get(k))
    elif st.settings_cat == "prefs" and st.cursor in PREF_ROWS:
        attr = PREF_ROWS[st.cursor]
        st.editing, st.edit_from = True, (attr, getattr(st, attr))
    return st


def _end_edit(st):
    st.editing, st.edit_from = False, None
    return st


def _restore_pref(st):
    attr, old = st.edit_from
    setattr(st, attr, old)
    return [("set_theme", old)] if attr == "theme" else [("save_ui",)]


def _bind(st, action_id, new):
    b = pk.bindings(st.keys)
    b[action_id] = new
    st.keys = pk.overrides(b)
    a = pk.BY_ID[action_id]
    st.message = f"{pk.label(a)}: {new if new else 'no key'}"
    return st, [("save_ui",)]


def _capture_key(st, key):
    """The key pressed after Enter on a Keys row. Global and taken keys are refused, not bound."""
    action_id, st.key_capture = st.key_capture, None
    if key == "esc":
        st.message = "cancelled"
        return st, []
    if key in ("backspace", "delete"):
        return _bind(st, action_id, None)
    why = pk.refuse(pk.bindings(st.keys), action_id, key)
    if why:
        st.message = st.failed = why
        return st, []
    return _bind(st, action_id, key)


def _key_settings(st, key, current):
    if st.settings_focus == "menu":
        return _key_settings_menu(st, key)
    if key == "esc" and not st.pending and not st.editing:
        return _leave(st)
    return (_key_prefs if st.settings_cat == "prefs" else _key_audio)(st, key, current)


def _key_settings_menu(st, key):
    names = [c for c, _ in SETTINGS_CATS]
    if key in ("up", "down"):
        n = max(0, min(len(names) - 1, names.index(st.settings_cat) + (1 if key == "down" else -1)))
        if names[n] != st.settings_cat:
            st.settings_cat, st.cursor = names[n], 0
    elif key in ("enter", "right"):
        st.settings_focus = "pane"
    return st, []


def _key_audio(st, key, current):
    last = len(_rows(st)) - 1
    st.cursor = max(0, min(last, st.cursor))
    on_buttons = audio_row(st) == ("buttons",)
    if st.editing and audio_setting(st.cursor, st.audio_rows):
        return _edit_clock(st, key, current)
    _end_edit(st)
    if key in ("up", "down"):
        st.cursor = max(0, min(last, st.cursor + (1 if key == "down" else -1)))
    elif on_buttons and key == "left" and st.clock_button == 0:
        return _leave(st)
    elif on_buttons and key in ("left", "right"):
        st.clock_button = max(0, min(len(CLOCK_BUTTONS) - 1, st.clock_button + (1 if key == "right" else -1)))
    elif on_buttons and key == "enter":
        button = CLOCK_BUTTONS[st.clock_button]
        if button == "Save":
            return _save_clock(st)
        if button == "Reset":
            return _reset_clock(st, current)
        if button == "Restart":  # restart WirePlumber, after the popover (the R shortcut's arrow path)
            st.popup, st.popup_button = ("restart",), None
            return st, []
        return _apply_clock(st, current)
    elif key == "left":
        return _leave(st)
    elif key == "enter":
        _begin_edit(st)  # only value rows open; a row that shows something does nothing
    elif key == "esc":
        st.pending, st.message = {}, "reverted"
    return st, []


def _edit_clock(st, key, current):
    """An open clock row: ←→ step it (pending), Enter applies, Esc puts it back, ↑↓ keep it pending."""
    s = audio_setting(st.cursor, st.audio_rows)
    if key in ("left", "right"):
        base = st.pending.get(s.key, current.get(s.key))
        new = cycle(s, base if isinstance(base, int) else s.choices[0], 1 if key == "right" else -1)
        if new == current.get(s.key):
            st.pending.pop(s.key, None)
        else:
            st.pending[s.key] = new
        st.message = ""
    elif key == "enter":
        return _apply_clock(_end_edit(st), current)
    elif key == "esc":
        had, old = st.edit_from
        if had:
            st.pending[s.key] = old
        else:
            st.pending.pop(s.key, None)
        _end_edit(st).message = "cancelled"
    elif key in ("up", "down"):
        _end_edit(st).cursor = max(0, min(len(_rows(st)) - 1, st.cursor + (1 if key == "down" else -1)))
    return st, []


def _apply_clock(st, current):
    if not st.pending:
        return st, []
    try:
        warnings = validate(current, st.pending)
        changes = ordered_changes(current, st.pending)
    except ValueError as e:
        st.message = st.failed = str(e)
        return st, []
    st.pending, st.message = {}, "; ".join(warnings)
    return st, [("set_many", changes)]


def _save_clock(st):
    """Save keeps the live clock for the next PipeWire start; unapplied edits would make that ambiguous."""
    if st.pending:
        st.message = st.failed = "apply first: Save keeps the live clock settings"
        return st, []
    return st, [("save_clock",)]


def _reset_clock(st, current):
    """PipeWire's stock clock into pending: the rows show *, and Apply writes them."""
    for k, v in CLOCK_DEFAULTS.items():
        if current.get(k) == v:
            st.pending.pop(k, None)
        else:
            st.pending[k] = v
    st.message = "PipeWire defaults loaded; Apply writes them" if st.pending else "already at PipeWire's defaults"
    return st, []


def _key_prefs(st, key, current):
    st.cursor = max(0, min(RESET_KEYS_ROW, st.cursor))
    if st.editing and st.cursor in PREF_ROWS:
        return _edit_pref(st, key)
    _end_edit(st)
    if key in ("up", "down"):
        st.cursor = max(0, min(RESET_KEYS_ROW, st.cursor + (1 if key == "down" else -1)))
    elif key == "left":
        return _leave(st)
    elif st.cursor in PREF_ROWS and key == "enter":
        _begin_edit(st)
    elif KEYS_ROW <= st.cursor < RESET_KEYS_ROW and key in ("enter", "backspace", "delete"):
        kind, a = BINDING_ROWS[st.cursor - KEYS_ROW]
        if kind == "fixed":
            st.message = f"{a[1]} is a fixed key ({a[2]}); MIDI/CC binding comes later"
            return st, []
        if key == "enter":
            st.key_capture = a.id
            st.message = f"press a key for {pk.label(a)} (Esc cancels, Backspace unbinds)"
            return st, []
        return _bind(st, a.id, None)
    elif st.cursor == RESET_KEYS_ROW and key == "enter":
        st.popup = ("reset_keys",)
    return st, []


def _edit_pref(st, key):
    """An open Display row: ←→ change it live, Enter keeps it, Esc puts it back, ↑↓ keep it."""
    if key in ("left", "right"):
        return _step_pref(st, key)
    if key == "enter":
        _end_edit(st)
    elif key == "esc":
        actions = _restore_pref(st)
        _end_edit(st).message = "cancelled"
        return st, actions
    elif key in ("up", "down"):
        _end_edit(st).cursor = max(0, min(RESET_KEYS_ROW, st.cursor + (1 if key == "down" else -1)))
    return st, []


def _step_pref(st, key):
    if st.cursor in (BAR_ROW, METERS_ROW, SHOW_HIDDEN_ROW, MONITORS_ROW, KEY_BOX_ROW):  # on/off rows
        attr = PREF_ROWS[st.cursor]
        setattr(st, attr, not getattr(st, attr))
        return st, [("save_ui",)]
    if st.cursor in (FPS_ROW, FALL_ROW, HOLD_ROW):
        name, choices = {FPS_ROW: ("meter_fps", METER_FPS), FALL_ROW: ("meter_fall", METER_FALL),
                         HOLD_ROW: ("peak_hold", PEAK_HOLD)}[st.cursor]
        n = max(0, min(len(choices) - 1, choices.index(getattr(st, name)) + (1 if key == "right" else -1)))
        setattr(st, name, choices[n])
        return st, [("save_ui",)]
    st.theme = THEME_NAMES[(THEME_NAMES.index(st.theme) + (1 if key == "right" else -1)) % len(THEME_NAMES)]
    return st, [("set_theme", st.theme)]


def _key_top(st, key, current):
    step = {"down": 1, "up": -1, "pgdn": 10, "pgup": -10}.get(key)
    if step:
        st.top_scroll = max(0, st.top_scroll + step)
    return st, []


def devices_view(inv, config, owned=(), clash=(), pending=()):
    """Everything the Devices tab draws: the live inventory, devices.json, and bus status (PipeWrench-owned,
    clashing with a bus made elsewhere, pending)."""
    return {"inv": inv, "config": config, "owned": set(owned), "clash": set(clash),
            "pending": list(pending)}


def _dev_lines(devices, st):
    """(kind, obj) per list line: card, virtual or off (headings), row (a bus), chan (a channel or monitor,
    listed read-only: channel work is on the Mixer), pending (a bus not yet created) or addbus. Cards on
    profile off carry no devices, so they wait at the bottom under Off."""
    inv, hidden = devices["inv"], set(devices["config"]["hidden"])

    def rows(rs):
        rs = list(rs) + (pi.monitor_rows(rs) if st.dev_monitors else [])
        return [("row" if r.kind == "virtual" else "chan", r) for r in rs if st.dev_show_hidden or r.name not in hidden]

    lines, off = [], []
    for card in inv.cards:
        if card.name in hidden and not st.dev_show_hidden:
            continue
        if card.active == "off":
            off.append(("card", card))
            continue
        lines.append(("card", card))
        lines += rows(card.rows)
    if inv.midi:  # read-only, like channels; nothing hides them
        lines += [("midi", None)] + [("chan", r) for r in inv.midi]
    lines.append(("virtual", None))
    lines += rows(inv.virtual)
    lines += [("pending", b) for b in devices["pending"]]
    lines.append(("addbus", None))
    return lines + ([("off", None)] + off if off else [])


DEV_HEADS = ("virtual", "off", "midi")  # box titles, never rows


def dev_selectable(devices, st):
    """Every line is a row (Skrappjaw, 2026-10-26: "every item navigable"); channels only show something."""
    return [line for line in _dev_lines(devices, st) if line[0] not in DEV_HEADS]


def _key_devices(st, key, devices):
    if devices is None:
        return st, []
    if st.dev_menu:
        return _key_menu(st, key, devices)
    if key == "a":
        return st, [("dev_prompt_bus",)]
    if key == "h":  # show hidden; nothing hides on a key (a stray h hid the GCS-8, Skrappjaw 2026-10-24)
        st.dev_show_hidden = not st.dev_show_hidden
        return st, [("save_ui",)]  # both survive a restart (Skrappjaw, 2026-10-26)
    if key == "m":
        st.dev_monitors = not st.dev_monitors
        return st, [("save_ui",)]
    items = dev_selectable(devices, st)
    if not items:
        return st, []
    st.dev_cursor = max(0, min(len(items) - 1, st.dev_cursor))
    kind, obj = items[st.dev_cursor]
    if key in ("up", "down"):
        st.dev_cursor = max(0, min(len(items) - 1, st.dev_cursor + (1 if key == "down" else -1)))
    elif key == "d":
        st.message = "default and rename are on the Mixer (1)"
    elif key == "enter":
        if kind == "addbus":
            return st, [("dev_prompt_bus",)]
        if kind != "chan":  # a channel only shows something; its work is on the Mixer
            _open_menu(st, kind, obj)
    elif key == "p":
        card_id = obj.id if kind == "card" else obj.card_id if kind == "row" else None
        card = next((c for c in devices["inv"].cards if c.id == card_id), None)
        if card is None:
            st.message = st.failed = "virtual devices have no card profile"
            return st, []
        _open_menu(st, "card", card)
    elif key == "x":
        if kind == "pending" or (kind == "row" and obj.kind == "virtual"):
            st.popup = ("dev_delete_bus", obj["name"] if kind == "pending" else obj.name)
        else:
            st.message = st.failed = "x deletes a virtual bus"
    elif key == "t":
        if kind == "row" and obj.kind != "monitor" and obj.direction in ("out", "duplex"):
            return st, [("dev_test", obj.name)]
        st.message = st.failed = "t plays a test noise on an output"
    return st, []


def _tap(event, selected):
    """A tap on a row selects it; a tap on the row already selected is Enter (Skrappjaw, 2026-10-26: "Tap the row",
    in place of Enter chips). A double-click is Enter either way, and only once."""
    return ["enter"] if event == "double" or (event == "click" and selected) else []


def handle_mouse(st, hits, y, x, event, current, devices=None, patch=None, mixer=None):
    """Map a mouse event onto the same keys the keyboard uses."""
    if st.popup:  # only its buttons take clicks; a click elsewhere leaves it up
        target = next((t for row, c0, c1, t in hits if row == y and c0 <= x < c1), None)
        if target and target[0] == "popup_btn" and event in ("click", "double"):
            return _key_popup(dataclasses.replace(st, popup_button=target[1]), "enter")
        return st, []
    target = next((t for row, c0, c1, t in hits if row == y and c0 <= x < c1), None)
    if event in ("wheel_up", "wheel_down"):
        # The wheel scrolls everywhere (Skrappjaw, 2026-10-24): ↑/↓ on the list in focus, wherever the mouse is,
        # and never changes a value. On Patch it scrolls the column it's over.
        if target and target[0] in ("patch_src", "patch_dst") and not st.patch_menu:
            st = dataclasses.replace(st, patch_side="src" if target[0] == "patch_src" else "dst")
        if target and target[0] == "settings_cat":
            st = dataclasses.replace(st, settings_focus="menu")
        return handle_key(st, "up" if event == "wheel_up" else "down", current, devices, patch, mixer)
    if target is None:
        return st, []
    kind = target[0]
    keys = []
    if kind == "tab":
        st, actions = _leave(dataclasses.replace(st))
        st.tab = target[1]
        return st, actions
    if kind == "settings_cat":
        open_ = st.settings_cat == target[1] and st.settings_focus == "menu"
        st, actions = _leave(dataclasses.replace(st))
        st.settings_cat = target[1]
        for k in _tap(event, open_):
            st, a = handle_key(st, k, current, devices, patch, mixer)
            actions += a
        return st, actions
    elif kind == "setting":
        selected = st.settings_focus == "pane" and target[1] == st.cursor
        if st.editing and target[1] != st.cursor:
            st = _end_edit(dataclasses.replace(st))
        st = dataclasses.replace(st, cursor=target[1], settings_focus="pane")
        keys = _tap(event, selected)
    elif kind == "arrow":  # a tap on ◀ ▶ is deliberate: it opens the row and steps it
        if not (st.editing and target[1] == st.cursor):
            st = _begin_edit(_end_edit(dataclasses.replace(st, cursor=target[1], settings_focus="pane")))
        keys = ["right" if target[2] > 0 else "left"] if event in ("click", "double") else []
    elif kind == "clock_button":  # a button: one click presses it, and closes an open row first
        st = _end_edit(dataclasses.replace(st, cursor=audio_index(("buttons",), st.audio_rows), clock_button=target[1],
                                           settings_focus="pane"))
        keys = ["enter"] if event in ("click", "double") else []
    elif kind == "top_row":  # Top only shows: a tap selects
        st = dataclasses.replace(st, top_scroll=target[1])
    elif kind == "key":  # a legend chip
        keys = [target[1]] if event in ("click", "double") else []
    elif kind == "mix_row":
        selected = target[1] == st.mix_cursor and not st.mix_menu
        st = dataclasses.replace(st, mix_cursor=target[1], mix_menu=None)
        keys = _tap(event, selected)
    elif kind == "mix_menu_item":
        st = dataclasses.replace(st, mix_menu_cursor=target[1])
        keys = ["enter"] if event in ("click", "double") else []
    elif kind == "dev_menu_item":
        st = dataclasses.replace(st, dev_menu_cursor=target[1])
        keys = ["enter"] if event in ("click", "double") else []
    elif kind == "device":
        selected = target[1] == st.dev_cursor and not st.dev_menu
        st = dataclasses.replace(st, dev_cursor=target[1], dev_menu=None)
        keys = _tap(event, selected)
    elif kind in ("patch_src", "patch_dst"):
        side = "src" if kind == "patch_src" else "dst"
        selected = st.patch_side == side and getattr(st, f"patch_{side}") == target[1] and not st.patch_menu
        st = dataclasses.replace(st, patch_side=side, patch_menu=False, **{f"patch_{side}": target[1]})
        keys = _tap(event, selected)
    elif kind == "patch_cell":
        selected = (st.patch_src, st.patch_dst) == target[1:3] and not st.patch_menu
        st = dataclasses.replace(st, patch_src=target[1], patch_dst=target[2], patch_menu=False)
        keys = _tap(event, selected)
    elif kind == "patch_slot":
        st = dataclasses.replace(st, patch_menu_cursor=target[1])
        keys = ["enter"] if event in ("click", "double") else []
    elif kind == "patch_pcell":
        selected = (st.patch_port_row, st.patch_port_col) == target[1:3]
        st = dataclasses.replace(st, patch_port_row=target[1], patch_port_col=target[2])
        keys = _tap(event, selected)
    actions = []
    for k in keys:
        st, a = handle_key(st, k, current, devices, patch, mixer)
        actions += a
    return st, actions


# --- rendering (view model) --------------------------------------------------

# The key box shows only what you can't guess (Skrappjaw, 2026-10-26: arrows, Enter and the tab keys are known "on
# instinct ... Main nav is also self explanatory with the numbers above"): letters, Esc, Space, q. What a row is
# and what Enter does there is the status line's job (_row_note). Chips stay clickable; a tap on a row is Enter.
TAB_KEYS = [("q", "quit")]
LEGENDS = {
    "mixer": [("Space", "mute"), ("v", "meters")] + TAB_KEYS,
    "settings": [("Esc", "menu")] + TAB_KEYS,
    "settings info": [("Esc", "menu")] + TAB_KEYS,  # an Audio row that only shows something
    "settings buttons": [("Esc", "menu")] + TAB_KEYS,
    "settings edit": [("Esc", "cancel")],
    "preferences edit": [("Esc", "cancel")],
    "settings menu": TAB_KEYS,
    "preferences": [("Esc", "menu")] + TAB_KEYS,
    "top": [("PgUp PgDn", "page")] + TAB_KEYS,
    "about": TAB_KEYS,
    "devices": [("a", "add bus"), ("m", "monitors"), ("h", "show hidden")] + TAB_KEYS,
    "keys": [("Bksp", "unbind"), ("Esc", "menu")] + TAB_KEYS,
    "keys capture": [("Esc", "cancel"), ("Bksp", "unbind")],
    "devices menu": [("Esc", "back")],
    "mixer menu": [("Esc", "back")],
    "patch cols": [("p", "ports"), ("g", "grid view"), ("m", "monitors")] + TAB_KEYS,
    "patch grid": [("p", "ports"), ("g", "column view"), ("m", "monitors")] + TAB_KEYS,
    "patch menu": [("Esc", "back")],
    "patch ports": [("p Esc", "back")] + TAB_KEYS,
}
# Devices letters that act on the selected row, before the view's own (Skrappjaw, 2026-10-26: keep both ways, list them).
DEV_ROW_KEYS = {"card": [("p", "card profile")], "pending": [("x", "delete bus")]}


def _dev_row_keys(devices, st):
    items = dev_selectable(devices, st) if devices else []
    if not items:
        return []
    kind, obj = items[max(0, min(len(items) - 1, st.dev_cursor))]
    if kind != "row":
        return DEV_ROW_KEYS.get(kind, [])
    test = [("t", "test noise")] if obj.kind != "monitor" and obj.direction in ("out", "duplex") else []
    return test + ([("x", "delete bus")] if obj.kind == "virtual" else [])


# What each Display row does, shown in the status line while it's selected (Skrappjaw, 2026-10-26).
PREF_NOTES = {THEME_ROW: "applies at once", FPS_ROW: "how often Mixer meters redraw",
              FALL_ROW: "how fast a meter drops after a peak",
              HOLD_ROW: "how long the peak marker stays at the loudest point before it falls",
              BAR_ROW: "the bar under each Mixer channel", METERS_ROW: "live levels on the Mixer",
              SHOW_HIDDEN_ROW: "hidden cards and buses on Devices, greyed",
              MONITORS_ROW: "each output's monitor on Devices, listed under it",
              KEY_BOX_ROW: "the key list at the bottom of the screen"}


# The same for Audio's rows: what each one means, in plain words (Skrappjaw, 2026-10-26).
AUDIO_NOTES = {
    "clock.force-rate": "PipeWire's rate for every app and device; off: its default or an app's request",
    "clock.force-quantum": "PipeWire's buffer for everything; off: the smallest an app asks for",
    "clock.min-quantum": "the smallest buffer an app may ask for; smaller = less delay, more CPU",
    "clock.max-quantum": "the largest buffer an app may ask PipeWire for",
    "log.level": "how much PipeWire writes to its log (the journal), for chasing bugs; never changes the sound"}


# Audio's rows that only show something, explained (Skrappjaw, 2026-10-26: PipeWire is "server that manages the things").
AUDIO_INFO_NOTES = {
    "server": "PipeWire is the server every app and device goes through",
    "realtime": "realtime lets PipeWire's audio jump the queue, so it doesn't drop out when the computer is busy",
    "now": "the clock PipeWire runs right now, read from pw-top",
    "buffer_by": "what decides the buffer: forced, or the smallest an app asks for, kept between the limits",
    "rate_by": "what decides the rate: forced, PipeWire's default, or an app's request from the allowed rates",
    "allowed": "the rates an app may switch PipeWire to (clock.allowed-rates in PipeWire's config)",
    "defaults": "PipeWire's own rate and buffer, when nothing forces them and no app asks",
    "buttons": "Apply: live now · Save: for the next PipeWire start · Reset: PipeWire's defaults · Restart: WirePlumber"}
RT_NOTES = {"pipewire group": "the group allowed realtime priority; you need to be in it",
            "realtime limit": "the realtime priority PipeWire was given when it started; 0 is none",
            "audio threads": "PipeWire's audio threads, and whether each runs realtime",
            "portal refusals": "apps asking the desktop portal for realtime and refused; harmless while PipeWire is realtime"}


# Every row explains itself on the status line: what it is, then what you can do with it (Skrappjaw, 2026-10-26:
# "Everything gets an explainer and a defined set of actions"). Enter's list is read off the row's own menu.
SETTINGS_CAT_NOTES = {"audio": "PipeWire, your sound cards and the clock · Enter to open",
                      "prefs": "how PipeWrench looks, and its key bindings · Enter to open"}
MENU_NOTES = {"default": "every app that follows the default moves with it", "mute": "mutes or unmutes it",
              "test": "plays a short test sound on it", "rename": "a name of your own; the device's id never changes",
              "profile": "the card's profiles, on Devices", "boost": "lets the volume go past 100% (it can clip)",
              "hide": "hides it from the lists; Show hidden (h) brings it back", "unhide": "shows it again",
              "create": "makes the bus in PipeWire now", "delete": "removes the bus, after asking",
              "back": "closes the menu"}


def _row_note(st, current=None, realtime=None, rates=None, block=None, devices=None, patch=None, mixer=None):
    """The status-line note for the row under the cursor, if it has one."""
    try:
        if st.tab == "settings":
            return _settings_note(st, current, realtime, rates, block)
        if st.tab == "mixer":
            return _mixer_note(st, mixer)
        if st.tab == "devices":
            return _devices_note(st, devices)
        if st.tab == "patch":
            return _patch_note(st, patch)
        if st.tab == "top":
            return _top_note(st, block)
    except (IndexError, KeyError, ValueError, AttributeError, TypeError):
        return None  # data mid-refresh: the tab's hint stands in for a moment
    return None


def _enter_list(items):
    """ · Enter: a, b, c from a menu's [(label, op)]: Back left out, profiles counted, short words."""
    profiles = sum(1 for _, op in items if op[0] == "profile" and len(op) > 1)
    labels = [f"{profiles} profile{'' if profiles == 1 else 's'}"] if profiles else []
    for label, op in items:
        if op[0] == "back" or (op[0] == "profile" and len(op) > 1):
            continue
        word = label.split(":")[0].rstrip("…").replace("Set as system default", "set as default")
        word = word.replace("Volume boost", "boost")
        labels.append(word[0].lower() + word[1:])
    return f" · Enter: {', '.join(labels)}" if labels else ""


def _settings_note(st, current, realtime, rates, block):
    if st.settings_focus != "pane":
        return SETTINGS_CAT_NOTES.get(st.settings_cat)
    if st.settings_cat == "prefs":
        return _prefs_note(st)
    if st.editing:
        return "←→ to change · Enter applies · Esc puts it back"
    note = _audio_note(st, current, realtime, rates, block)
    if audio_setting(st.cursor, audio_rows(realtime, rates)):
        note = f"{note} · Enter to change"
    return note


def _prefs_note(st):
    if st.editing:
        return "←→ to change · Enter keeps it · Esc puts it back"
    if st.cursor in PREF_NOTES:
        return f"{PREF_NOTES[st.cursor]} · Enter to change"
    if KEYS_ROW <= st.cursor < RESET_KEYS_ROW:
        kind, x = BINDING_ROWS[st.cursor - KEYS_ROW]
        if kind == "fixed":
            return "a fixed key: the tab keys can't be rebound"
        return f"{pk.label(x)}: {pk.key_for(pk.bindings(st.keys), x.id) or 'no key'} · Enter, then the new key"
    if st.cursor == RESET_KEYS_ROW:
        return "puts every key back to its default · Enter"
    return None


def _mixer_note(st, mx):
    if mx is None:
        return None
    if st.mix_menu:
        items = mix_menu_items(mx, st)
        label, op = items[st.mix_menu_cursor] if items else ("", ("",))
        return "already the system default · Enter closes the menu" if label.startswith("★") else MENU_NOTES.get(op[0])
    kind, section, row = mixer_selectable(mx)[st.mix_cursor]
    if kind == "app":  # the row shows its name; the note says what it is
        what = f"an app {'playing' if section == 'playback' else 'recording'}"
    else:
        default = " · ★ the default" if _is_default(mx, section, row) else ""
        what = f"{'playback' if section == 'playback' else 'recording'}{default}"
    if row.volume is None:
        return f"{what} · no volume here" + (" · connect it on Patch (3)" if row.media == "midi" else "")
    return f"{what} · ←→ volume{_enter_list(_mix_items(mx, kind, section, row))}"


def _channel_note(obj, devices):
    if obj.kind == "midi":
        return f"a MIDI {'output' if obj.direction == 'out' else 'input'} port · connect it on Patch (3)"
    if obj.kind == "monitor":
        return "a monitor: what an output plays, for recording it (OBS desktop audio) · connect it on Patch (3)"
    what = {"out": "an output", "in": "an input"}.get(obj.direction, "a channel")
    hidden = " · hidden" if obj.name in devices["config"]["hidden"] else ""
    default = " · ★ the default" if obj.default else ""
    return f"{what}{default} · {obj.state or 'unknown'}{hidden} · default, rename and test it on the Mixer (1)"


def _devices_note(st, devices):
    if devices is None:
        return None
    if st.dev_menu:
        items = dev_menu_items(devices, st)
        op = items[st.dev_menu_cursor][1] if items else ("",)
        return "switches the card to this profile" if op[0] == "profile" else MENU_NOTES.get(op[0])
    kind, obj = dev_selectable(devices, st)[st.dev_cursor]
    if kind == "addbus":
        return "a new bus: a virtual device apps can play into · Enter to name it"
    if kind == "chan":
        return _channel_note(obj, devices)
    probe = dataclasses.replace(st)
    _open_menu(probe, kind, obj)
    actions = _enter_list(dev_menu_items(devices, probe))
    if kind == "card":
        return f"sound card · profile {obj.active}{actions}"
    if kind == "pending":
        return f"a bus not made yet{actions}"
    use = {"out": "apps can play into it", "in": "apps can record from it"}.get(obj.direction,
                                                                             "apps can play into it and record from it")
    return f"a bus: {use}{actions}"


def _patch_note(st, g):
    if g is None:
        return None
    srcs, s, d = _patch_pair(g, st)
    if s is None:
        return None
    if st.patch_menu:
        return "pick the inputs to connect · Enter connects them"
    if st.patch_ports:
        a, b = s.ports[st.patch_port_row], d.ports[st.patch_port_col]
        on = any(x is a and y is b and linked for x, y, linked in pp.port_pairs(g, s, d))
        return f"{a.channel} → {b.channel}: {'linked · Enter unlinks' if on else 'not linked · Enter links'}"
    state = g.state(s, d)
    if st.patch_view == "cols" and st.patch_side == "src":
        n = len(g.targets(s))
        return f"a source: {n} link{'' if n == 1 else 's'} · Enter or → to its destinations"
    if state == "n/a":
        return _pair_status(g, s, d).strip()
    verb = "connects" if state == "none" else "disconnects"
    return f"{_pair_status(g, s, d).strip()} · Enter {verb} · p port by port"


def _audio_note(st, current, realtime, rates, block):
    ids = audio_rows(realtime, rates)
    rid = ids[st.cursor] if 0 <= st.cursor < len(ids) else None
    if rid is None:
        return None
    s = audio_setting(st.cursor, ids)
    if s:
        return AUDIO_NOTES.get(s.key)
    if rid[0] == "server" and st.pw_down:
        return "PipeWire isn't answering: apps have no sound until it's back"
    if rid[0] == "rt":
        return RT_NOTES.get(rid[1])
    if rid[0] == "fix":
        return ("drops calls: apps lose sound while PipeWire restarts; a reboot does it cleanly"
                if prt.fix(realtime)[rid[1]].startswith("for now") else "the fix, for you to run in a terminal")
    if rid[0] == "device":
        return _device_note(next((r for r in rates or [] if r.label == rid[1]), None),
                            _mix_rate(current or {}, _audio_now(block, rates)))
    return AUDIO_INFO_NOTES.get(rid[0])


def _device_note(r, mix):
    if r is None:
        return "the sound cards PipeWire manages, read from pw-dump"
    can = f" · can run {pwrates.describe(r.stream)}" if r.known else " · can run: not reported"
    rate, vs = _device_vs(r, mix)
    if r.status == "off":
        return f"{r.label} is off (its card profile); turn it on from Devices (2)"
    if vs.startswith("✓"):
        return f"✓ runs at PipeWire's rate: audio passes straight through{can}"
    if vs.startswith("⇄"):
        return (f"⇄ runs at {_khz(rate)}; PipeWire resamples it to {_khz(mix) if mix else 'its rate'}"
                f" (a little CPU, usually inaudible){can}")
    return f"{r.status or 'not running'}: PipeWire wakes it when something plays{can}"


# The status line under the legend: messages, else a short hint (never keys).
HINTS = {"mixer": "volume and mute act at once · ★ is the default playback or recording device",
         "devices": "cards and buses here · rename, default and test a channel on the Mixer (1)",
         "patch": "links made here are live only; saved patch sets come with scenes"}


def _key_box(items, width):
    """The key legend as a box: each key a chip with its slug beside it, flowed onto as many lines as the
    width needs. Returns (lines, chips): chips are (box line, col0, col1, key) for chips one key presses."""
    inner = max(10, width - 4)
    rows, cur, spans = [], "", []
    for key, slug in items:
        gap = "   " if cur else ""
        if cur and len(cur) + len(gap) + len(key) + 3 + len(slug) > inner:
            rows.append((cur, spans))
            cur, spans, gap = "", [], ""
        c0 = len(cur) + len(gap)
        cur += f"{gap} {key}  {slug}"
        spans.append((c0, c0 + len(key) + 2, key))
    if cur:
        rows.append((cur, spans))
    lines, chips = [("┌─ Keys " + "─" * max(0, width - 9) + "┐", "dim")], []
    for text, sp in rows:
        lines.append(("│ " + text.ljust(inner) + " │", ("normal", [(c0 + 2, c1 + 2, "chip") for c0, c1, _ in sp])))
        chips += [(len(lines) - 1, c0 + 2, c1 + 2, _chip_key(k)) for c0, c1, k in sp if _chip_key(k)]
    lines.append(("└" + "─" * max(0, width - 2) + "┘", "dim"))
    return lines, chips


def _chip_key(label):
    """The key a click on a chip presses; None for chips that name several (↑↓, Tab/1-5)."""
    if label.endswith(" Esc"):  # "← Esc", "p Esc": a tap is Esc, so the way back stays tappable
        return "esc"
    return {"Enter": "enter", "Esc": "esc", "Space": " "}.get(label) or (label if len(label) == 1 else None)


def _bound(items, screen, keys):
    """A legend with each shortcut chip showing its bound key; unbound or clashing shortcuts drop out."""
    b, out = pk.bindings(keys), []
    for k, slug in items:
        head, _, rest = k.partition(" ")
        a = next((a for a in pk.ACTIONS if a.screen == screen and a.default == head), None)
        if a is None:
            out.append((k, slug))
        elif pk.key_for(b, a.id):
            out.append((pk.key_for(b, a.id) + (" " + rest if rest else ""), slug))
    return out


def legend(st, devices=None):
    items = _legend(st)
    if st.tab == "devices" and not st.dev_menu and not st.popup:
        items = _dev_row_keys(devices, st) + items
    return _bound(items, st.tab, st.keys) if st.tab in pk.SCREENS and st.keys else items


def _legend(st):
    if st.popup:
        return [("Esc", "cancel")]
    if st.tab == "devices" and st.dev_menu:
        return LEGENDS["devices menu"]
    if st.tab == "mixer" and st.mix_menu:
        return LEGENDS["mixer menu"]
    if st.tab == "settings" and st.key_capture:
        return LEGENDS["keys capture"]
    if st.tab == "settings" and st.settings_focus == "menu":
        return LEGENDS["settings menu"]
    if st.tab == "settings" and st.editing:
        return LEGENDS["preferences edit" if st.settings_cat == "prefs" else "settings edit"]
    if st.tab == "settings" and st.settings_cat == "prefs":
        return LEGENDS["keys" if KEYS_ROW <= st.cursor < RESET_KEYS_ROW else "preferences"]
    if st.tab == "settings" and audio_row(st) == ("buttons",):
        return LEGENDS["settings buttons"]
    if st.tab == "settings" and not audio_setting(st.cursor, st.audio_rows):
        return LEGENDS["settings info"]
    if st.tab == "patch" and st.patch_menu:
        return LEGENDS["patch menu"]
    if st.tab == "patch":
        return LEGENDS["patch ports" if st.patch_ports else f"patch {st.patch_view}"]
    return LEGENDS[st.tab]


_MARK_ROLE = {"●": "dst", "◐": "dst_partial"}  # connection dots; dst/dst_partial are ok/pending outside pipeOS
_BARS = ("selected", "anchor", "bar_src", "anchor_src", "bar_dst", "anchor_dst")


class _Canvas:
    def __init__(self, width):
        self.width = width
        self.lines = []
        self.hits = []

    def add(self, text, style="normal"):
        """style is a role, or (base role, [(c0, c1, role), ...]) drawn in order over the base."""
        text = text[:self.width]
        base, spans = style if isinstance(style, tuple) else (style, [])
        chosen = {a for a, b, _ in spans if b == a + 1}  # a one-cell span over a dot: a status dot, not a connection
        marks = [(n, n + 1, _MARK_ROLE[ch]) for n, ch in enumerate(text) if ch in _MARK_ROLE and n not in chosen]
        if marks:
            spans = list(spans)
            for c0, c1, role in marks:  # a dot under a bar keeps its colour on the bar's background
                under = next((r for a, b, r in reversed(spans) if a <= c0 < b), base if base in _BARS else None)
                if under in _BARS:
                    spans.append((c0, c1, f"{role}@{under}"))
            style = (base, marks + spans)
        self.lines.append((text, style))
        return len(self.lines) - 1

    def hit(self, row, c0, c1, target):
        if c0 < self.width:
            self.hits.append((row, c0, min(c1, self.width), target))


# Popovers (Skrappjaw, 2026-10-26): warnings and questions in a box that stays until answered, with no
# shortcut keys. {kind: (title, [(button, op)], default button)}; text comes from popup_view.
POPUPS = {
    "restart": ("Restart WirePlumber", [("Restart", "ok"), ("Cancel", "cancel")], 1),
    "restart_labels": ("Restart WirePlumber", [("Restart now", "ok"), ("Cancel", "cancel")], 1),
    "dev_delete_bus": ("Delete bus", [("Delete", "ok"), ("Cancel", "cancel")], 1),
    "dev_profile": ("Switch profile", [("Switch", "ok"), ("Cancel", "cancel")], 1),
    "reset_keys": ("Reset keys", [("Reset", "ok"), ("Cancel", "cancel")], 1),
    "quit": ("Quit", [("Quit", "ok"), ("Cancel", "cancel")], 1),
    "pw_down": ("PipeWire not answering", [("OK", "close")], 0),
    "vol_boost": ("Volume past 100%", [("Go louder", "go"), ("Don't ask again", "always"), ("Cancel", "cancel")], 2),
}
PW_DOWN_TEXT = "PipeWire stopped answering. PipeWrench keeps the last screen and checks again every 5 s."


def popup_view(st, devices=None):
    """(title, text, [(button, op)]) for the open popover."""
    title, buttons, _ = POPUPS[st.popup[0]]
    kind = st.popup[0]
    text = (PW_DOWN_TEXT if kind == "pw_down" else f"{st.popup[3]} is at 100%. Louder can clip." if kind == "vol_boost"
            else _confirm_question(st.popup, devices))
    return title, text, buttons


def popup_cursor(st):
    return POPUPS[st.popup[0]][2] if st.popup_button is None else st.popup_button


def _key_popup(st, key):
    """←→ choose, Enter picks, Esc cancels; any other key leaves the box up (it can't be missed)."""
    kind = st.popup
    buttons = POPUPS[kind[0]][1]
    if key in ("left", "right"):
        st.popup_button = max(0, min(len(buttons) - 1, popup_cursor(st) + (1 if key == "right" else -1)))
        return st, []
    if key not in ("enter", "esc"):
        return st, []
    op = buttons[popup_cursor(st)][1] if key == "enter" else "cancel"
    st.popup = st.popup_button = None
    if op == "cancel":
        st.message = "cancelled"
        return st, []
    if op == "close":
        return st, []
    if kind[0] == "vol_boost":  # ("vol_boost", node id, node name, label, the +5% command)
        _, nid, name, label, cmd = kind
        st.vol_unlocked = st.vol_unlocked | {nid}
        return st, [("mix_cmd", list(cmd))] + ([("boost", name, True, label)] if op == "always" else [])
    if kind == ("reset_keys",):
        st.keys, st.message = {}, "keys reset to the defaults"
        return st, [("save_ui",)]
    # confirm tuples name their action, except the two v1 ones
    return st, [{"restart": ("restart_wireplumber",), "restart_labels": ("restart_wireplumber",)}.get(kind[0], kind)]


def _overlay_popup(cv, st, devices, width, height):
    """Draw the open popover centred over the finished screen; only its buttons take clicks. Buttons sit on
    one row, or one per row when the screen is too narrow for that."""
    title, text, buttons = popup_view(st, devices)
    chosen = popup_cursor(st)
    labels = [f"> {b} <" if i == chosen else f"[ {b} ]" for i, (b, _) in enumerate(buttons)]  # marked, not only coloured
    one_row = "  ".join(labels)
    bw = max(8, min(width - 4, max(48, len(title) + 8, len(one_row) + 6)))
    inner = bw - 4
    btn_rows = [labels] if len(one_row) <= inner else [[label] for label in labels]
    blank = "│" + " " * (bw - 2) + "│"
    rows = ([f"┌─ {title[:bw - 6]} ".ljust(bw - 1, "─") + "┐", blank]
            + [f"│ {line.ljust(inner)} │" for line in textwrap.wrap(text, inner) or [""]] + [blank])
    first_btn = len(rows)
    for group in btn_rows:
        row = "  ".join(group)
        rows.append(f"│ {row.center(inner)[:inner]} │")
    rows += [blank, "└" + "─" * (bw - 2) + "┘"]
    y0, x0 = max(0, (height - len(rows)) // 2), (width - bw) // 2
    cv.hits = []
    i = 0
    for n, row in enumerate(rows):
        y = y0 + n
        if y >= len(cv.lines):
            break
        text0, style = cv.lines[y]
        base, spans = style if isinstance(style, tuple) else (style, [])
        line = text0.ljust(width)[:width]
        spans = list(spans) + [(x0, x0 + bw, "popup")]
        if first_btn <= n < first_btn + len(btn_rows):
            group = btn_rows[n - first_btn]
            c = x0 + 2 + (inner - len("  ".join(group))) // 2
            for label in group:
                spans.append((c, c + len(label), "popup_sel" if i == chosen else "popup"))
                cv.hit(y, c, c + len(label), ("popup_btn", i))
                c, i = c + len(label) + 2, i + 1
        cv.lines[y] = (line[:x0] + row + line[x0 + bw:], (base, spans))


def _confirm_question(confirm, devices=None):
    kind, arg = confirm[0], (confirm[1] if len(confirm) > 1 else None)
    if kind == "dev_profile":
        card = next((c for c in (devices or {"inv": pi.Inventory([], [], {})})["inv"].cards if c.id == arg), None)
        return f"Switch {card.label if card else 'the card'} to {confirm[3]}? Audio on that card drops for a moment."
    return {"restart": "Restart WirePlumber?",
            "restart_labels": "Restart WirePlumber now to show it? Apps like Discord may lose their audio.",
            "dev_delete_bus": f"Delete bus {arg!r}? It disappears from apps now.",
            "reset_keys": "Reset every key to its default?",
            "quit": "Quit PipeWrench?"}[kind]


def render_screen(st, current, block, width, height, devices=None, patch=None, mixer=None, media=None,
                  about=None, realtime=None, rates=None):
    if st.tab == "settings":  # the rows are what's drawn: the legend and note read the same list as the page
        st = dataclasses.replace(st, audio_rows=audio_rows(realtime, rates))
    cv = _Canvas(width)
    text, spans = " ", []
    for tab in TABS:
        label = f"[{TAB_TITLES[tab]}]" if tab == st.tab else f" {TAB_TITLES[tab]} "
        spans.append((len(text), len(text) + len(label), tab))
        text += label + " "
    active = next((c0, c1, "anchor") for c0, c1, tab in spans if tab == st.tab)
    row = cv.add(text, ("title", [active]))
    for c0, c1, tab in spans:
        cv.hit(row, c0, c1, ("tab", tab))
    cv.add(f" {st.pw_down} ".center(width, "─")[:width] if st.pw_down else "─" * width,
           "alert" if st.pw_down else "dim")
    footer, chips = _key_box(legend(st, devices), width) if st.key_box else ([], [])  # hidden: the body gets its lines
    body_h = height - len(cv.lines) - MEDIA_BOX_H - 1 - len(footer)  # the now-playing box and the status line
    if st.tab == "mixer":
        _render_mixer(cv, st, mixer, body_h)
    elif st.tab == "devices":
        _render_devices(cv, st, devices, body_h)
    elif st.tab == "patch":
        _render_patch(cv, dataclasses.replace(st), patch, body_h)
    elif st.tab == "about":
        _render_about(cv, about, st.theme)
    elif st.tab == "settings":
        _render_settings(cv, st, current, body_h, realtime, rates, block)
    else:
        _render_top(cv, st, block, body_h)
    body_end = height - MEDIA_BOX_H - 1 - len(footer)
    cv.lines = cv.lines[:body_end]
    cv.hits = [h for h in cv.hits if h[0] < body_end]
    while len(cv.lines) < body_end:
        cv.add("")
    top = len(cv.lines)
    for text, style in footer:
        cv.add(text, style)
    for n, c0, c1, k in chips:
        cv.hit(top + n, c0, c1, ("key", k))
    _media_box(cv, media)  # under the Keys box, above the status line (Skrappjaw's punch list, 2026-10-26)
    if st.message:  # a full-width band; the symbol carries it on one-colour themes (Skrappjaw, 2026-10-26)
        bad = st.message == st.failed
        cv.add(f" {'✖' if bad else '✓'} {st.message}".ljust(width), "band_alert" if bad else "band_ok")
    else:
        note = _row_note(st, current, realtime, rates, block, devices, patch, mixer)
        cv.add(_fit(f" {note or HINTS.get(st.tab, '')}", width).ljust(width), "status_line")  # a calm full-width bar
    if st.popup:
        _overlay_popup(cv, st, devices, width, height)
    return cv.lines, cv.hits


_MEDIA_CHIPS = [("*", "play"), ("-", None), ("+", "vol"), ("<", "prev"), (">", "next")]  # numpad order
_STATUS = {"Playing": ("▶", "ok"), "Paused": ("❚❚", "pending"), "Stopped": ("■", "dim")}


def _media_box(cv, media):
    """The now-playing line in its own box, framed like the Keys box (Skrappjaw, 2026-10-26: "media player needs
    its own box to separate from the rest of the ui")."""
    inner = _Canvas(max(10, cv.width - 4))
    _media_bar(inner, media)
    text, style = inner.lines[0]
    base, spans = style if isinstance(style, tuple) else (style, [])
    cv.add("┌─ Now playing " + "─" * max(0, cv.width - 16) + "┐", "dim")
    row = cv.add("│ " + text.ljust(inner.width) + " │", (base, [(a + 2, b + 2, r) for a, b, r in spans]))
    for _, c0, c1, target in inner.hits:
        cv.hit(row, c0 + 2, c1 + 2, target)
    cv.add("└" + "─" * max(0, cv.width - 2) + "┘", "dim")


MEDIA_BOX_H = 3


def _media_bar(cv, media):
    """What's playing, and its keys as clickable chips, on one line of cv.
    media: {"player": pwmedia.Player or None, "position": microseconds now}. When space runs short the
    song is cut first, then the time; the keys always fit."""
    right, chips, joined = "", [], False
    for k, slug in _MEDIA_CHIPS:
        gap = "" if not right or joined else "  "
        c0 = len(right) + len(gap)
        right += f"{gap} {k} " + (f" {slug}" if slug else "")
        chips.append((c0, c0 + 3, k))
        joined = slug is None  # "- +" read as one pair
    player = (media or {}).get("player")
    room = cv.width - len(right) - 3
    if player is None:
        left, spans = "  ♪ nothing playing", [(2, 19, "dim")]
    else:
        glyph, role = _STATUS.get(player.status, ("■", "dim"))
        head = f"  {glyph} {player.label} · "
        song = " — ".join(x for x in (player.title, player.artist) if x) or "untitled"  # title first: it survives a cut
        when = f"   {pm.clock(media.get('position', player.position))} / {pm.clock(player.length)}" if player.length else ""
        if room - len(head) - len(when) < 8:
            when = ""
        left = head + _fit(song, max(1, room - len(head) - len(when))) + when
        spans = [(2, 2 + len(glyph), role), (3 + len(glyph), len(head) - 3, "title")]
    if room < 12:  # too narrow for both: keys win
        left, spans = "", []
    left = left[:max(0, room)]
    c0 = cv.width - len(right) - 1
    row = cv.add(left.ljust(c0) + right,
                 ("normal", [sp for sp in spans if sp[1] <= len(left)] + [(c0 + a, c0 + b, "chip") for a, b, _ in chips]))
    for a, b, k in chips:
        cv.hit(row, c0 + a, c0 + b, ("key", k))


def _has_cursor(style):
    """Is this the cursor's line? Boxed lines carry the cursor bar as a span."""
    base, spans = style if isinstance(style, tuple) else (style, [])
    return base in ("selected", "anchor") or any(r in ("selected", "anchor") for _, _, r in spans)


def _render_settings(cv, st, current, body_h, realtime=None, rates=None, block=None):
    """The category menu on the left, the open category on the right, scrolled so its cursor row shows.
    While the menu has the keys the pane shows no cursor and the open category is an anchor."""
    focused = st.settings_focus == "pane"
    view = st if focused else dataclasses.replace(st, cursor=-1)
    pane = _Canvas(max(10, cv.width - MENU_W))
    if st.settings_cat == "prefs":
        _render_prefs(pane, view)
    else:
        _render_audio(pane, view, current, realtime, rates, block)
    marked = [n for n, (_, style) in enumerate(pane.lines) if _has_cursor(style)]
    cursor = marked[0] if marked else 0
    end = marked[-1] if marked else 0  # a wrapped row is several lines: show all of it, then a little more
    focus = min(max(cursor + 3, end + 1), cursor + max(1, body_h) - 1)
    rows = list(_window(len(pane.lines), min(len(pane.lines) - 1, focus), max(1, body_h)))
    top = len(cv.lines)
    for i in range(max(len(rows), len(SETTINGS_CATS))):
        menu, spans = " " * (MENU_W - 1), []
        if i < len(SETTINGS_CATS):
            name, label = SETTINGS_CATS[i]
            menu = f" {label}".ljust(MENU_W - 1)
            if name == st.settings_cat:
                spans.append((0, MENU_W - 1, "anchor" if focused else "selected"))
        spans.append((MENU_W - 1, MENU_W, "dim"))
        text, style = pane.lines[rows[i]] if i < len(rows) else ("", "normal")
        if isinstance(style, tuple):
            spans += [(MENU_W, MENU_W + len(text), style[0])] + [(MENU_W + a, MENU_W + b, r) for a, b, r in style[1]]
        elif style != "normal":
            spans.append((MENU_W, MENU_W + len(text), style))
        row = cv.add(menu + "│" + text, ("normal", spans))
        if i < len(SETTINGS_CATS):
            cv.hit(row, 0, MENU_W - 1, ("settings_cat", SETTINGS_CATS[i][0]))
    for r, c0, c1, t in pane.hits:
        if r in rows:
            cv.hit(top + rows.index(r), MENU_W + c0, MENU_W + c1, t)


def _khz(rate):
    return f"{rate / 1000:g} kHz"


def pipewire_now(block):
    """What PipeWire runs now, from a pw-top block: [(quantum, rate, driver)] for each running driver;
    [] when nothing plays; None before pw-top has answered."""
    if block is None:
        return None
    return [(r.quant, r.rate, r.name) for r in block if r.state == "R" and not r.follower and r.quant > 0]


def _boxed(cv, title, draw):
    """Draw a section into its own box, framed like the Keys box (Skrappjaw, 2026-10-26: "visually break up what is
    on screen"). draw(inner) fills an inner canvas; its styles and hits move inside the frame."""
    inner = _Canvas(max(10, cv.width - 4))
    draw(inner)
    cv.add("┌─ " + title + " " + "─" * max(0, cv.width - len(title) - 5) + "┐",
           ("dim", [(3, 3 + len(title), "title")]))  # a section heading: title, on a dim frame (ui-conventions)
    top = len(cv.lines)
    for text, style in inner.lines:
        base, spans = style if isinstance(style, tuple) else (style, [])
        bar = [(2, 2 + inner.width, base)] if base != "normal" else []
        frame = [(0, 1, "dim"), (inner.width + 3, inner.width + 4, "dim")]
        cv.add("│ " + text.ljust(inner.width) + " │", ("normal", frame + bar + [(a + 2, b + 2, r) for a, b, r in spans]))
    for row, c0, c1, target in inner.hits:
        cv.hit(top + row, c0 + 2, c1 + 2, target)
    cv.add("└" + "─" * max(0, cv.width - 2) + "┘", "dim")


def _box_lines(title, entries, width, title_role="title"):
    """A section framed like _boxed, for pages that scroll a list of (text, style, idx) themselves (Mixer, Devices,
    Top; Skrappjaw, 2026-10-26: "box separation was the right move"). Styles and the cursor bar move inside the frame;
    idx stays with its line, so hits and scrolling work as before."""
    inner = max(10, width - 4)
    out = [("┌─ " + title + " " + "─" * max(0, width - len(title) - 5) + "┐", ("dim", [(3, 3 + len(title), title_role)]),
            None)]
    for text, style, idx in entries:
        base, spans = style if isinstance(style, tuple) else (style, [])
        bar = [(2, 2 + inner, base)] if base != "normal" else []
        frame = [(0, 1, "dim"), (inner + 3, inner + 4, "dim")]
        out.append(("│ " + text[:inner].ljust(inner) + " │",
                    ("normal", frame + bar + [(a + 2, min(b, inner) + 2, r) for a, b, r in spans if a < inner]), idx))
    out.append(("└" + "─" * max(0, width - 2) + "┘", "dim", None))
    return out


def _render_audio(cv, st, current, realtime=None, rates=None, block=None):
    """The server, the devices it manages, the clock you change, then Advanced, each in a box (Skrappjaw, 2026-10-26:
    "what then status ... a heirarchy of importance"). Every line is a row; see audio_rows."""
    now = _audio_now(block, rates)
    ids = audio_rows(realtime, rates)
    at = {rid: n for n, rid in enumerate(ids)}
    _boxed(cv, "PipeWire", lambda c: _render_pipewire(c, st, current, now, realtime, at))
    cv.add("")
    _boxed(cv, "Devices", lambda c: _render_device_rates(c, st, rates, _mix_rate(current, now), at))
    cv.add("")
    _boxed(cv, "Clock", lambda c: _render_clock(c, st, current, now, rates, at))
    cv.add("")
    _boxed(cv, "Advanced", lambda c: _clock_row(c, st, current, at[("log",)], len(SETTINGS) - 1))


def _audio_now(block, rates):
    """pipewire_now, with each driver named by its device label."""
    now = pipewire_now(block)
    labels = {n: label for r in rates or [] for n, label in r.nodes}
    return [(q, r, labels.get(name, name)) for q, r, name in now] if now else now


def _mix_rate(current, now):
    """PipeWire's rate: the running one, else the forced one, else its default."""
    if now:
        return now[0][1]
    return current.get("clock.force-rate") or current.get("clock.rate")


def _info_row(cv, st, idx, label, value, role="normal", mark=None, more=()):
    """A row that shows something: the label, then its status (Skrappjaw, 2026-10-26: "its the what then status").
    mark colours the value's first character (● ◐ ✖ ✓ ⇄). A value too wide wraps under itself; more is extra
    lines of the same row. Every line is part of the row: the cursor bar covers them all and a click selects it."""
    head = f"{'> ' if idx == st.cursor else '  '}{label:<20} "
    room = max(10, cv.width - len(head))
    lines = []
    for part in [value, *more]:
        lines += textwrap.wrap(part, room, break_on_hyphens=False) or [""]
    for n, text in enumerate(lines):
        line = (head if n == 0 else " " * len(head)) + text
        if idx == st.cursor:
            style = "selected"
        else:
            spans = [(len(head), len(head) + 1, mark)] if mark and n == 0 else []
            style = (role, spans) if spans else role
        row = cv.add(line, style)
        cv.hit(row, 0, max(len(line), cv.width), ("setting", idx))


def _render_pipewire(cv, st, current, now, realtime, at):
    """The server: is it answering, is it realtime, and the clock it runs right now."""
    if st.pw_down:
        _info_row(cv, st, at[("server",)], "Server", "✖ not answering", mark="alert")
    else:
        _info_row(cv, st, at[("server",)], "Server", "● running", mark="ok")
    if realtime is None:
        _info_row(cv, st, at[("realtime",)], "Realtime", "checking…", "dim")
    elif not isinstance(realtime, prt.Status):
        _info_row(cv, st, at[("realtime",)], "Realtime", "not checked (fixtures)", "dim")
    else:
        ok = realtime.ok
        _info_row(cv, st, at[("realtime",)], "Realtime", "● realtime" if ok else "◐ not realtime",
                  mark="ok" if ok else "pending")
        for label, mark, text in prt.rows(realtime):  # the dot is a status (ok or a problem), never a portal colour
            _info_row(cv, st, at[("rt", label)], "  " + label, f"{mark} {text}" if mark else text,
                      "normal" if mark else "dim", mark="ok" if mark == "●" else "pending" if mark else None)
        if not ok:
            for n, line in enumerate(prt.fix(realtime)):
                label, text = ("  for now", line.split(": ", 1)[1]) if line.startswith("for now") else ("  fix", line)
                _info_row(cv, st, at[("fix", n)], label, text, "pending")
    rate = current.get("clock.force-rate") or current.get("clock.rate")
    quantum = current.get("clock.force-quantum") or current.get("clock.quantum")
    if now is None:
        _info_row(cv, st, at[("now",)], "Running now", "waiting for pw-top…", "dim")
    elif not now:
        _info_row(cv, st, at[("now",)], "Running now",
                  f"idle · when something plays: {_khz(rate) if isinstance(rate, int) else '?'} · {quantum} samples")
    else:
        distinct = list(dict.fromkeys((q, r) for q, r, _ in now))
        shown = [(q, r, None) for q, r in distinct] if len(distinct) == 1 else now  # drivers that agree: one line
        lines = [f"{_khz(r)} · {q} samples · {latency_ms(q, r)}" + (f"   {name}" if name else "") for q, r, name in shown]
        _info_row(cv, st, at[("now",)], "Running now", lines[0], more=lines[1:])
    lo, hi = current.get("clock.min-quantum"), current.get("clock.max-quantum")
    buf = "forced" if current.get("clock.force-quantum") else f"smallest an app asks for ({lo}–{hi})"
    if current.get("clock.force-rate"):
        why = "forced"
    elif now and now[0][1] != current.get("clock.rate"):
        why = "an app asked for it (allowed rates)"
    else:
        why = "PipeWire's default"
    _info_row(cv, st, at[("buffer_by",)], "  buffer set by", buf, "dim")
    _info_row(cv, st, at[("rate_by",)], "  rate set by", why, "dim")


def _render_clock(cv, st, current, now, rates, at):
    """What you change: the overrides and limits, what PipeWire falls back to, and the buttons."""
    for i in range(len(SETTINGS) - 1):
        _clock_row(cv, st, current, at[("setting", i)], i, now)
    allowed = current.get("clock.allowed-rates")
    allowed = ", ".join(_khz(r) for r in allowed) if isinstance(allowed, list) else "?"
    _info_row(cv, st, at[("allowed",)], "Allowed rates", allowed)
    dr, dq = current.get("clock.rate"), current.get("clock.quantum")
    _info_row(cv, st, at[("defaults",)], "Defaults", f"{_khz(dr) if isinstance(dr, int) else '?'} · {dq} samples")
    try:
        warnings = validate(current, st.pending, rates) if st.pending else []
    except ValueError as e:
        warnings = [str(e)]
    for w in warnings:  # a warning about unapplied edits; it goes when they do
        for n, line in enumerate(textwrap.wrap(w, max(10, cv.width - 4))):
            cv.add(("  ! " if n == 0 else "    ") + line, "alert")
    _clock_buttons(cv, st, at[("buttons",)])


def _clock_row(cv, st, current, idx, sidx, now=()):
    """One value row at Audio row idx for SETTINGS[sidx]: ◀ value ▶, * when pending, then the latency it stands
    for, or for an override that's off, what's running now instead (Skrappjaw, 2026-10-26: "no indicator for the
    current setting"). now: pipewire_now; None until pw-top answers."""
    s = SETTINGS[sidx]
    merged = {**current, **st.pending}
    value = st.pending.get(s.key, current.get(s.key))
    mark = "*" if s.key in st.pending else " "
    head = f"{'> ' if idx == st.cursor else '  '}{s.label:<20} "
    line = f"{head}◀ {display_value(s, value):^14} ▶ {mark}"
    if s.key in FORCE_KEYS and value == 0 and now is None:
        extra = "→ waiting for pw-top"  # not a default's latency dressed as a live value
    elif s.key in FORCE_KEYS and value == 0 and now:
        q, r, _ = now[0]
        extra = f"→ {_khz(r)} now" if s.key == "clock.force-rate" else f"→ {q} now ({latency_ms(q, r)})"
    else:
        extra = row_latency(s, value, merged)
    spans = [(len(line) - 1, len(line), "pending")] + ([(len(line), len(line) + len(extra) + 2, "dim")] if extra else [])
    line += f"  {extra}" if extra else ""
    arrow_l, arrow_r = len(head), len(head) + 17
    arrows = _arrow_spans(arrow_l, arrow_r, idx == st.cursor, idx == st.cursor and st.editing)
    if idx == st.cursor:  # an open row is drawn as the anchor, so editing reads differently from selecting
        style = ("anchor" if st.editing else "selected", arrows)
    else:
        style = ("normal", spans + arrows)
    row = cv.add(line, style)
    cv.hit(row, 0, arrow_l, ("setting", idx))
    cv.hit(row, arrow_l, arrow_l + 1, ("arrow", idx, -1))
    cv.hit(row, arrow_l + 1, arrow_r, ("setting", idx))
    cv.hit(row, arrow_r, arrow_r + 1, ("arrow", idx, +1))


def _device_vs(r, mix):
    """A card's rate, and how it meets PipeWire: the same rate passes straight through, another is resampled."""
    rate = r.live or r.running  # PipeWire's rate for a running device, else the kernel's momentary rate
    if r.status == "off" or rate is None:
        return rate, ""
    return rate, "✓ same as PipeWire" if rate == mix else "⇄ resampled"


def _render_device_rates(cv, st, rates, mix, at):
    """Every card, then its status, its rate and how it meets PipeWire. What it can run is on the status line."""
    if not rates:
        why = "no cards found" if rates == [] else "not read (fixtures, or pw-dump didn't answer)"
        _info_row(cv, st, at[("device", None)], "Device", why, "dim")
        return
    width = max([len(r.label) for r in rates] + [20])
    cv.add(f"  {'Device':<{width}} {'Status':<11} {'Rate':<9} vs PipeWire", "title")  # headers are title (ui-conventions)
    for r in rates:
        idx = at[("device", r.label)]
        rate, vs = _device_vs(r, mix)
        head = f"{'> ' if idx == st.cursor else '  '}{r.label:<{width}} "
        cols = f"{head}{r.status or '?':<11} {_khz(rate) if rate else '—':<9} "
        line = f"{cols}{vs}".rstrip()
        if idx == st.cursor:
            style = "selected"
        else:
            spans = [(len(cols), len(cols) + 1, "ok")] if vs.startswith("✓") else \
                    [(len(cols), len(cols) + len(vs), "pending")] if vs.startswith("⇄") else []
            style = ("dim" if r.status == "off" else "normal", spans)
        row = cv.add(line, style)
        cv.hit(row, 0, max(len(line), cv.width), ("setting", idx))


def _clock_buttons(cv, st, idx):
    """Apply · Save · Reset · Restart as chips. On their row ←→ picks one and Enter presses it; a click presses it."""
    on_row = st.cursor == idx
    line, spans, chips = "> " if on_row else "  ", [], []
    for n, label in enumerate(CLOCK_BUTTONS):
        c0 = len(line)
        line += f"[ {label} ]"
        if on_row and n == st.clock_button:  # only the cursor lights a button; chip is for key legends
            spans.append((c0, len(line), "selected"))
        chips.append((c0, len(line), n))
        line += "  "
    row = cv.add(line.rstrip(), ("normal", spans))
    for c0, c1, n in chips:
        cv.hit(row, c0, c1, ("clock_button", n))


def _render_prefs(cv, st):
    """Display and Key Bindings, each in a box (Skrappjaw, 2026-10-26: "box up the sections there as well")."""
    _boxed(cv, "Display", lambda c: _render_display(c, st))
    cv.add("")
    _boxed(cv, "Key Bindings", lambda c: _render_keys(c, st))


def _render_display(cv, st):
    _display_row(cv, st, THEME_ROW, "Theme", theme_label(st.theme))
    _display_row(cv, st, FPS_ROW, "Meter frame rate", f"{st.meter_fps} fps")
    _display_row(cv, st, FALL_ROW, "Meter fall", f"{st.meter_fall} dB/s")
    _display_row(cv, st, HOLD_ROW, "Peak hold", f"{st.peak_hold:g} s" if st.peak_hold else "off")
    _display_row(cv, st, BAR_ROW, "Volume bar", "on" if st.volume_bar else "off")
    _display_row(cv, st, METERS_ROW, "Meters", "on" if st.meters else "off")
    _display_row(cv, st, SHOW_HIDDEN_ROW, "Show hidden devices", "on" if st.dev_show_hidden else "off")
    _display_row(cv, st, MONITORS_ROW, "Monitors on Devices", "on" if st.dev_monitors else "off")
    _display_row(cv, st, KEY_BOX_ROW, "Keys box", "on" if st.key_box else "off")


def binding_label(a):
    """An action's row label on Key Bindings: capitalised like the Global rows."""
    return a.label[:1].upper() + a.label[1:]


def _render_keys(cv, st):
    """Key Bindings (Skrappjaw, 2026-10-26): Action · Key · MIDI/CC under a heading per view. Global keys are fixed;
    an action shows its key, * when changed, and why it can't fire when it clashes. MIDI/CC is a placeholder."""
    cv.add(f"  {'Action':<32} {'Key':^6}   {'MIDI/CC':^7}", "title")  # column headers are title; the box is the heading
    b = pk.bindings(st.keys)
    bad = pk.clashes(b)
    heading = None
    for n, (kind, x) in enumerate(BINDING_ROWS):
        group = "Global" if kind == "fixed" else pk.HEADINGS[x.screen]
        if group != heading:
            heading = group
            cv.add(f"  {group}", "title")
        idx = KEYS_ROW + n
        if kind == "fixed":
            label, k, mark, note, style = x[1], x[2], " ", "", "dim"
        else:
            label = binding_label(x)
            k = "…" if st.key_capture == x.id else (b[x.id] or "—")
            mark = "*" if b[x.id] != x.default else " "
            note = bad.get(x.id) or ("press a key" if st.key_capture == x.id else "")
            style = "alert" if x.id in bad else "normal"
        line = f"{'> ' if st.cursor == idx else '  '}  {label:<30} {k:^6} {mark} {'—':^7}  {note}".rstrip()
        row = cv.add(line, "selected" if st.cursor == idx else style)
        cv.hit(row, 0, len(line), ("setting", idx))
    line = f"{'> ' if st.cursor == RESET_KEYS_ROW else '  '}Reset all keys"
    row = cv.add(line, "selected" if st.cursor == RESET_KEYS_ROW else "normal")
    cv.hit(row, 0, len(line), ("setting", RESET_KEYS_ROW))


def _arrow_spans(arrow_l, arrow_r, on_cursor, open_):
    """◀ ▶ are grey until Enter opens the row (Skrappjaw, 2026-10-26); on the cursor bar they keep its background."""
    if open_:
        return []
    role = "dim@selected" if on_cursor else "dim"
    return [(arrow_l, arrow_l + 1, role), (arrow_r, arrow_r + 1, role)]


def _display_row(cv, st, idx, label, value):
    head = f"{'> ' if st.cursor == idx else '  '}{label:<20} "
    base = ("anchor" if st.editing else "selected") if st.cursor == idx else "normal"
    arrow_l, arrow_r = len(head), len(head) + 17
    spans = _arrow_spans(arrow_l, arrow_r, st.cursor == idx, st.cursor == idx and st.editing)
    row = cv.add(f"{head}◀ {value:^14} ▶", (base, spans))
    cv.hit(row, 0, arrow_l, ("setting", idx))
    cv.hit(row, arrow_l, arrow_l + 1, ("arrow", idx, -1))
    cv.hit(row, arrow_l + 1, arrow_r, ("setting", idx))
    cv.hit(row, arrow_r, arrow_r + 1, ("arrow", idx, +1))


# --- About ---------------------------------------------------------------------

# The wordmarks from notes/about-art.md (TAAG: ANSI Compact, Pagga). The widest that fits is drawn.
LOGO_ANSI = [
    "█████▄ ▄▄ ▄▄▄▄  ▄▄▄▄▄ ██     ██ ▄▄▄▄  ▄▄▄▄▄ ▄▄  ▄▄  ▄▄▄▄ ▄▄ ▄▄",
    "██▄▄█▀ ██ ██▄█▀ ██▄▄  ██ ▄█▄ ██ ██▄█▄ ██▄▄  ███▄██ ██▀▀▀ ██▄██",
    "██     ██ ██    ██▄▄▄  ▀██▀██▀  ██ ██ ██▄▄▄ ██ ▀██ ▀████ ██ ██",
]
LOGO_PAGGA = [
    "░█▀█░▀█▀░█▀█░█▀▀░█░█░█▀▄░█▀▀░█▀█░█▀▀░█░█",
    "░█▀▀░░█░░█▀▀░█▀▀░█▄█░█▀▄░█▀▀░█░█░█░░░█▀█",
    "░▀░░░▀▀▀░▀░░░▀▀▀░▀░▀░▀░▀░▀▀▀░▀░▀░▀▀▀░▀░▀",
]
LOGO_PLAIN = ["P I P E W R E N C H"]
# Skrappjaw's own wordmark for dark and light (2026-10-25): the P's stem runs down as the wrench handle.
# ▒ and ░ are the shading, drawn dim; █ is the lettering.
LOGO_WRENCH = [
    '▒██████ ▒██▒████  ▒████▒██  ▒█▒█████ ▒████▒████  ▒███▒██ ▒█',
    '     ░██      ▒██▒██   ▒██  ▒█▒██ ▒█▒██   ▒██▒██▒██  ▒██ ▒█',
    '▒███████▒██▒█████▒████ ▒██  ▒█▒█████▒████ ▒██ ▒█▒██  ▒█████',
    '  ▒██░█ ▒██▒██   ▒██   ▒██▒█▒█▒██ █ ▒██   ▒██ ▒█▒██  ▒██ ▒█',
    '  ▒██   ▒██▒██    ▒████▒███▒██▒██ ██ ▒████▒██ ▒█ ▒███▒██ ▒█',
    '  ▒██                                                      ',
    '  ▒██                                                      ',
    '  ▒█                                                       ',
    '  ▒█                                                       ',
    '  ▒                                                        ',
]
# A wordmark per theme (Skrappjaw, 2026-10-24), from figlet fonts: Doom for crowbar, Computer for every Pipe Boy,
# Slant for Vaporware. Rows are padded to one width so the logo centres as a block.
LOGO_DOOM = [
    '______ _             _    _                     _     ',
    '| ___ (_)           | |  | |                   | |    ',
    '| |_/ /_ _ __   ___ | |  | |_ __ ___ _ __   ___| |__  ',
    "|  __/| | '_ \\ / _ \\| |/\\| | '__/ _ \\ '_ \\ / __| '_ \\ ",
    '| |   | | |_) |  __/\\  /\\  / | |  __/ | | | (__| | | |',
    '\\_|   |_| .__/ \\___| \\/  \\/|_|  \\___|_| |_|\\___|_| |_|',
    '        | |                                           ',
    '        |_|                                           ',
]
LOGO_COMPUTER = [
    '8""""8               8   8  8                             ',
    '8    8 e  eeeee eeee 8   8  8 eeeee  eeee eeeee eeee e   e',
    '8eeee8 8  8   8 8    8e  8  8 8   8  8    8   8 8  8 8   8',
    '88     8e 8eee8 8eee 88  8  8 8eee8e 8eee 8e  8 8e   8eee8',
    '88     88 88    88   88  8  8 88   8 88   88  8 88   88  8',
    '88     88 88    88ee 88ee8ee8 88   8 88ee 88  8 88e8 88  8',
]
LOGO_SLANT = [
    '    ____  _          _       __                     __  ',
    '   / __ \\(_)___  ___| |     / /_______  ____  _____/ /_ ',
    '  / /_/ / / __ \\/ _ \\ | /| / / ___/ _ \\/ __ \\/ ___/ __ \\',
    ' / ____/ / /_/ /  __/ |/ |/ / /  /  __/ / / / /__/ / / /',
    '/_/   /_/ .___/\\___/|__/|__/_/   \\___/_/ /_/\\___/_/ /_/ ',
    '       /_/                                              ',
]
# Pipecraft says PIPECRAFT at every width: ANSI Shadow (blocky, like Minecraft's logo), then Pagga.
LOGO_PIPECRAFT = [
    "██████╗ ██╗██████╗ ███████╗ ██████╗██████╗  █████╗ ███████╗████████╗",
    "██╔══██╗██║██╔══██╗██╔════╝██╔════╝██╔══██╗██╔══██╗██╔════╝╚══██╔══╝",
    "██████╔╝██║██████╔╝█████╗  ██║     ██████╔╝███████║█████╗     ██║   ",
    "██╔═══╝ ██║██╔═══╝ ██╔══╝  ██║     ██╔══██╗██╔══██║██╔══╝     ██║   ",
    "██║     ██║██║     ███████╗╚██████╗██║  ██║██║  ██║██║        ██║   ",
    "╚═╝     ╚═╝╚═╝     ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝        ╚═╝   ",
]
LOGO_PIPECRAFT_SMALL = [
    "░█▀█░▀█▀░█▀█░█▀▀░█▀▀░█▀▄░█▀█░█▀▀░▀█▀",
    "░█▀▀░░█░░█▀▀░█▀▀░█░░░█▀▄░█▀█░█▀▀░░█░",
    "░▀░░░▀▀▀░▀░░░▀▀▀░▀▀▀░▀░▀░▀░▀░▀░░░░▀░",
]


_LOGO_CHAINS = {"wrench": [LOGO_WRENCH], "doom": [LOGO_DOOM], "slant": [LOGO_SLANT], "computer": [LOGO_COMPUTER],
                "ansi": [LOGO_ANSI], "pipecraft": [LOGO_PIPECRAFT, LOGO_PIPECRAFT_SMALL, ["P I P E C R A F T"]]}


def logo_chain(theme):
    """Wordmarks to try, widest first; the last always fits. The theme file names its logo."""
    chain = _LOGO_CHAINS[THEME_LOGOS.get(theme, "ansi")]
    return chain if len(chain) > 1 else chain + [LOGO_PAGGA, LOGO_PLAIN]
ABOUT_LINK = "github.com/Skrappjaw/pipewrench"
ABOUT_CMDS = {"pipewire": ["pipewire", "--version"], "wireplumber": ["wireplumber", "--version"]}


def about_info(run):
    """Versions and paths for the About tab; "?" for anything that can't be read."""
    info = {}
    for name, argv in ABOUT_CMDS.items():
        try:
            rc, out = run(argv)
        except (OSError, ValueError):
            rc, out = 1, ""
        m = re.search(rf"lib{name} (\d+\.\d+\.\d+)", out or "")
        info[name] = m.group(1) if rc == 0 and m else "?"
    folder = str(ui_path().parent)
    home = str(Path.home())
    info["config"] = "~" + folder[len(home):] if folder.startswith(home + "/") else folder
    return info


def _render_about(cv, about, theme="dark"):
    about = about or {}
    chain = logo_chain(theme)
    logo = next((lg for lg in chain[:-1] if len(lg[0]) + 4 <= cv.width), chain[-1])
    pad = max(0, (cv.width - len(logo[0])) // 2)
    cv.add("")
    for row in logo:
        plate = [(pad + n, pad + n + 1, "dim") for n, ch in enumerate(row) if ch in "░▒"]
        cv.add(" " * pad + row, ("title", plate) if plate else "title")
    cv.add("")
    tagline = "terminal control for PipeWire"
    cv.add(" " * max(0, (cv.width - len(tagline)) // 2) + tagline, "dim")
    cv.add("")
    rows = [("PipeWire", about.get("pipewire", "?")), ("WirePlumber", about.get("wireplumber", "?")),
            ("Config", about.get("config", "?")), ("Source", ABOUT_LINK)]
    width = max(13 + len(v) for _, v in rows)
    left = max(2, (cv.width - width) // 2)
    for label, value in rows:
        cv.add(" " * left + f"{label:<13}{value}", ("normal", [(left, left + len(label), "title")]))


_TOP_HDR = f"{'S':<2}{'ID':>5}{'QUANT':>7}{'RATE':>7}{'WAIT':>9}{'BUSY':>9}{'W/Q':>6}{'B/Q':>6}{'ERR':>5}  {'FORMAT':<16} NAME"


def top_groups(block):
    """[(title, rows)]: a box per running driver with its followers, then Idle for drivers with nothing running
    (Skrappjaw, 2026-10-26: "A box per driver"). Rows keep pw-top's order inside each box."""
    groups, idle = [], []
    for r in block or []:
        if not r.follower:
            if r.state == "R":
                groups.append((f"{r.name} · {r.quant} @ {r.rate / 1000:g}k", [r]))
            else:
                idle.append(r)
        elif groups:
            groups[-1][1].append(r)
        else:
            idle.append(r)
    return groups + ([("Idle", idle)] if idle else [])


def top_rows(block):
    """Every Top row in screen order: st.top_scroll is the cursor into this."""
    return [r for _, rows in top_groups(block) for r in rows]


def _render_top(cv, st, block, body_h):
    """Every line a row (QA pass, 2026-10-26: "every item navigable"), boxed by driver."""
    if block is None:
        cv.add("  waiting for pw-top…", "dim")
        return
    rows = top_rows(block)
    st.top_scroll = max(0, min(len(rows) - 1, st.top_scroll)) if rows else 0
    lines, n = [], 0
    for title, members in top_groups(block):
        entries = [(f"  {_TOP_HDR}", "title", None)]  # column headers, over the cursor gutter: drawn, never a row
        for r in members:
            name = f"+ {r.name}" if r.follower else r.name
            text = (f"{'> ' if n == st.top_scroll else '  '}{r.state:<2}{r.id:>5}{r.quant:>7}{r.rate:>7}{r.wait:>9}"
                    f"{r.busy:>9}{r.wq:>6}{r.bq:>6}{r.err:>5}  {r.format:<16} {name}")
            style = "selected" if n == st.top_scroll else "alert" if r.err > 0 else "normal" if r.state == "R" else "dim"
            entries.append((text, style, n))
            n += 1
        lines += _box_lines(_fit(title, max(10, cv.width - 8)), entries, cv.width)
    at = next((k for k, line in enumerate(lines) if line[2] == st.top_scroll), 0)
    at = min(len(lines) - 1, at + 1)
    start = 0 if at < body_h else at - body_h + 1
    for text, style, idx in lines[start:start + max(1, body_h)]:
        row = cv.add(text, style)
        if idx is not None:
            cv.hit(row, 0, cv.width, ("top_row", idx))


def _top_note(st, block):
    rows = top_rows(block)
    if not rows:
        return None
    r = rows[max(0, min(len(rows) - 1, st.top_scroll))]
    err = f"{r.err} dropout{'' if r.err == 1 else 's'} (ERR) since it started"
    if r.follower:
        driver = next((d for d in reversed(rows[:rows.index(r)]) if not d.follower), None)
        return f"follows {driver.name if driver else 'its driver'}: runs on its clock · {err}"
    if r.state != "R":
        return "an idle driver: nothing is running on it now"
    n = sum(1 for _, ms in top_groups(block) if ms[0] is r for m in ms[1:])
    return f"driver: sets the clock for {n} follower{'' if n == 1 else 's'} · {r.quant} samples at {_khz(r.rate)} · {err}"


# Always on screen in the Devices tab; the bottom help line is too narrow and gives way to messages.


def _fit(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def _device_line_text(kind, obj, devices, selected):
    prefix = "> " if selected else "  "
    if kind == "card" and obj.active == "off":
        return f"{prefix}{obj.label} · off", "dim"
    if kind == "card":
        return f"{prefix}{obj.label} · profile {obj.active or '?'}", "title"
    if kind in ("virtual", "off", "midi"):
        return f"  {'MIDI' if kind == 'midi' else kind.title()}", "title"
    if kind == "addbus":
        return f"{prefix}+ Add bus…", "dim"
    if kind == "pending":
        return f"{prefix}+ duplex {_fit(obj['label'], 30):<30} pending: Enter → Create now  {obj['name']}", "pending"
    if obj.kind == "midi":  # no state, volume or node of its own: every ALSA MIDI port shares the bridge
        text = f"{prefix}  {obj.direction:<6} {_fit(obj.label, 30):<30} {f'hw:{obj.alsa_card}':<9}" \
               + (f"  {pi.connections(obj)}" if obj.inputs or obj.outputs else "")
        return text, "normal"
    c = devices["config"]
    vol = "muted" if obj.mute else "" if obj.volume is None else f"{round(obj.volume * 100)}%"
    pending = c["labels"].get(obj.name)
    hidden = obj.name in c["hidden"]
    tags = [t for t, on in (("PipeWrench", obj.name in devices["owned"]), ("made outside PipeWrench", obj.name in devices["clash"]),
                            ("hidden", hidden), (f"label → {pending}", bool(pending) and pending != obj.label)) if on]
    text = (f"{prefix}{'★' if obj.default else ' '} {obj.direction:<6} {_fit(obj.label, 30):<30} "
            f"{obj.state or '?':<9} {vol:<6} {obj.id:>5}" + (f"  [{', '.join(tags)}]" if tags else "")
            + (f"  {pi.connections(obj)}" if obj.inputs or obj.outputs else ""))  # tags first: they're rare, and matter
    return text, ("dim" if hidden else "normal", _tag_spans(text))


def _tag_spans(text):
    """Tags in their roles: a clash reads as danger, a label not yet written as pending."""
    spans = []
    for tag, role in (("made outside PipeWrench", "alert"), ("label → ", "pending")):
        a = text.find(tag)
        if a >= 0:
            b = min((n for n in (text.find(",", a), text.find("]", a)) if n >= 0), default=len(text))
            spans.append((a, b, role))
    return spans


DEV_COLUMNS = f"    {'Dir':<6} {'Name':<30} {'Status':<9} {'Vol':<6} {'Node':>5}"


def _render_devices(cv, st, devices, body_h):
    """A box per card (its profile, then its channels), then MIDI, Buses and Off (Skrappjaw, 2026-10-26: "box
    separation was the right move. Along with every item navigable")."""
    if devices is None:
        cv.add("  loading devices…", "dim")
        return
    selectable = dev_selectable(devices, st)
    cursor = max(0, min(len(selectable) - 1, st.dev_cursor)) if selectable else -1
    menu = _menu_target(devices, st) is not None
    groups, n_sel = [], 0

    def box(title, header=True):
        groups.append((title, [(DEV_COLUMNS, "title", None)] if header else []))
    for kind, obj in _dev_lines(devices, st):
        if kind in DEV_HEADS:
            box({"virtual": "Buses", "off": "Off", "midi": "MIDI"}[kind], header=kind == "virtual")
            continue
        if kind == "card" and obj.active != "off":
            groups.append((obj.label, []))
        idx, n_sel = n_sel, n_sel + 1
        sel = idx == cursor
        prefix = "> " if sel else "  "
        if kind == "card" and obj.active == "off":
            text, style = f"{prefix}{obj.label}", "dim"
        elif kind == "card":
            text, style = f"{prefix}{'Profile':<20} {obj.active or '?'}", "normal"
        else:
            text, style = _device_line_text("row" if kind == "chan" else kind, obj, devices, sel)
            if kind == "chan":  # in pipeOS its direction word is the portal colour
                role = {"out": "dir_out", "in": "dir_in"}.get(obj.direction)
                hidden = obj.name in devices["config"]["hidden"]
                style = ("dim" if hidden else "normal", [(4, 4 + len(obj.direction), role)] if role else [])
        entries = groups[-1][1]
        if kind == "card" and obj.active != "off":
            entries.append((text, "anchor" if sel and menu else "selected" if sel else style, idx))
            entries.append((DEV_COLUMNS, "title", None))  # column headers: drawn, never a row
        else:
            entries.append((text, "anchor" if sel and menu else "selected" if sel else style, idx))
        if sel and menu:
            title = obj.label if kind in ("row", "card") else kind.title() if obj is None else obj["name"]
            entries += _menu_lines(devices, st, title)
    lines = [line for title, entries in groups for line in _box_lines(title, entries, cv.width)]
    room = max(1, body_h)
    menu_at = max((n for n, line in enumerate(lines) if isinstance(line[2], tuple)), default=None)
    at = menu_at + 1 if menu_at is not None else next((n for n, line in enumerate(lines) if line[2] == cursor), 0)
    at = min(len(lines) - 1, at + (1 if menu_at is None else 0))  # a little room below, and the box foot
    start = 0 if at < room else at - room + 1
    for text, style, idx in lines[start:start + room]:
        row = cv.add(text, style)
        if isinstance(idx, tuple):
            cv.hit(row, 6, cv.width, ("dev_menu_item", idx[1]))
        elif idx is not None:
            cv.hit(row, 0, cv.width, ("device", idx))


# --- Mixer tab: everyday volume, mute and defaults (notes/mixer-plan.md) --------------------------

def mixer_view(inv, config, streams):
    """What the Mixer lists: devices (not hidden, not monitors) and app streams, by direction."""
    hidden = set(config["hidden"])
    hidden_cards = {c.id for c in inv.cards if c.name in hidden}
    devs = [r for r in inv.rows() if r.kind != "monitor" and r.name not in hidden and r.card_id not in hidden_cards]
    return {"defaults": inv.defaults, "boost": frozenset(config.get("boost", [])),
            "playback": ([r for r in devs if r.direction in ("out", "duplex")], [a for a in streams if a.direction == "out"]),
            "recording": ([r for r in devs if r.direction in ("in", "duplex")], [a for a in streams if a.direction == "in"])}


def _mixer_lines(mx):
    """[(kind, section, row)]: kind is head | sub | dev | app; dev and app lines are selectable."""
    lines = []
    for title, section in (("Playback", "playback"), ("Recording", "recording")):
        devs, apps = mx[section]
        lines.append(("head", title, None))
        lines += [("dev", section, r) for r in devs]
        if apps:
            lines.append(("sub", "Apps", None))
            lines += [("app", section, r) for r in apps]
    return lines


def mixer_selectable(mx):
    return [line for line in _mixer_lines(mx) if line[0] in ("dev", "app")]


def _is_default(mx, section, row):
    return row.name == mx["defaults"]["sink" if section == "playback" else "source"]


def _volume_up(st, row, mx):
    """→ stops at 100% unless the channel's boost is unlocked: saved (Don't ask again, or the channel menu)
    or for this session (Go louder). At 100% and locked it asks in a popover. A session unlock ends when the
    channel is seen under 100%; a channel already past 100% (set elsewhere) is never trapped."""
    saved = row.name in mx.get("boost", ())
    if saved or row.volume > pi.UNITY + 0.005 or (row.id in st.vol_unlocked and row.volume >= pi.UNITY - 0.005):
        return st, [("mix_cmd", pi.volume_cmd(row, "+5%"))]
    st.vol_unlocked = st.vol_unlocked - {row.id}
    if row.volume < pi.UNITY - 0.005:
        return st, [("mix_cmd", pi.volume_cmd(row, "+5%", limit=pi.UNITY))]
    st.popup, st.popup_button = ("vol_boost", row.id, row.name, row.label, tuple(pi.volume_cmd(row, "+5%"))), None
    return st, []


def _key_mixer(st, key, mx):
    if mx is None:
        return st, []
    if st.mix_menu:
        return _key_mix_menu(st, key, mx)
    items = mixer_selectable(mx)
    if not items:
        return st, []
    st.mix_cursor = max(0, min(len(items) - 1, st.mix_cursor))
    kind, section, row = items[st.mix_cursor]
    try:
        if key in ("up", "down"):
            st.mix_cursor = max(0, min(len(items) - 1, st.mix_cursor + (1 if key == "down" else -1)))
        elif key in ("left", "right"):
            if row.volume is None:
                st.message = st.failed = f"{row.label} has no volume control here"
                return st, []
            if key == "left":
                return st, [("mix_cmd", pi.volume_cmd(row, "-5%"))]
            return _volume_up(st, row, mx)
        elif key == " ":
            if row.mute is None:
                st.message = st.failed = f"{row.label} can't be muted here"
                return st, []
            return st, [("mix_cmd", pi.mute_cmd(row))]
        elif key == "enter":
            if _mix_items(mx, kind, section, row):
                st.mix_menu, st.mix_menu_cursor = (section, row.id), 0
            else:
                st.message = st.failed = f"{row.label} has nothing to set here"
        elif key == "v":
            st.meters = not st.meters
            st.message = "meters on" if st.meters else "meters off"
    except ValueError as e:
        st.message = st.failed = str(e)
    return st, []


def _mix_menu_target(mx, st):
    """(index, kind, section, row) of the row the open menu belongs to, or None if it has gone away."""
    if not st.mix_menu or mx is None:
        return None
    return next(((n, kind, section, row) for n, (kind, section, row) in enumerate(mixer_selectable(mx))
                 if (section, row.id) == st.mix_menu), None)


def _mix_items(mx, kind, section, row):
    """[(label, op)] for a row's menu. Setting the default moves every app that follows it, so it's never
    on a single key; it leads the menu so Enter, Enter still reaches it."""
    mute = [("Unmute" if row.mute else "Mute", ("mute",))] if row.mute is not None else []
    boost = ([(f"Volume boost: {'unlocked' if row.name in mx.get('boost', ()) else 'locked'}", ("boost",))]
             if row.volume is not None else [])
    if kind == "app":
        return mute + boost + [("Back", ("back",))] if mute or boost else []
    items = [("★ System default", ("back",)) if _is_default(mx, section, row)
             else ("Set as system default", ("default",))] + mute
    if section == "playback" and row.direction in ("out", "duplex"):
        items.append(("Test sound", ("test",)))
    items.append(("Rename…", ("rename",)))
    if row.card_id is not None:  # buses have no card
        items.append(("Card profile…", ("profile",)))
    return items + boost + [("Back", ("back",))]


def mix_menu_items(mx, st):
    target = _mix_menu_target(mx, st)
    return [] if target is None else _mix_items(mx, *target[1:])


def _key_mix_menu(st, key, mx):
    target = _mix_menu_target(mx, st)
    items = mix_menu_items(mx, st)
    if not items:
        st.mix_menu = None
        return st, []
    st.mix_cursor = target[0]
    st.mix_menu_cursor = max(0, min(len(items) - 1, st.mix_menu_cursor))
    if key in ("up", "down"):
        st.mix_menu_cursor = max(0, min(len(items) - 1, st.mix_menu_cursor + (1 if key == "down" else -1)))
        return st, []
    st.mix_menu = None
    if key != "enter":
        return st, []  # Esc or any other key closes the menu, like a cancelled prompt
    op, row = items[st.mix_menu_cursor][1][0], target[3]
    try:
        if op == "default":
            return st, [("mix_cmd", pi.default_cmd(row))]
        if op == "mute":
            return st, [("mix_cmd", pi.mute_cmd(row))]
    except ValueError as e:
        st.message = st.failed = str(e)
        return st, []
    if op == "boost":
        on = row.name not in mx.get("boost", ())
        if not on:
            st.vol_unlocked = st.vol_unlocked - {row.id}
        return st, [("boost", row.name, on, row.label)]
    if op == "test":
        return st, [("mix_test", row.name)]
    if op == "rename":
        return st, [("dev_rename", row.name, row.label)]
    if op == "profile":
        st.tab, st.dev_focus, st.dev_menu = "devices", ("card", row.card_id), None
    return st, []


METER_CAP = 12     # live meters at once; each costs the PipeWire daemon about 1% CPU
_METER = 16        # cells for -48…0 dBFS, 3 dB each
_METER_DB = 48


def meter_targets(mx, cursor):
    """{(section, node id): (serial, read a sink's monitor)} for the rows to meter: the selected row and
    every running one, up to METER_CAP. A suspended device carries no audio, and metering would wake it.
    Recording apps have nothing to read (their source has its own meter)."""
    picks = []
    for n, (kind, section, row) in enumerate(mixer_selectable(mx)):
        if not row.serial or (kind == "app" and section == "recording"):
            continue
        if n == cursor or row.state == "running":
            target = (row.serial, kind == "dev" and section == "playback") + ((row.meter_ports,) if row.meter_ports else ())
            picks.append((n != cursor, n, (section, row.id), target))
    return {k: target for _, _, k, target in sorted(picks)[:METER_CAP]}


MIDI_CAP = 4      # MIDI activity taps at once (one pw-mididump each)
MIDI_LIT = 0.3    # seconds the dot stays lit after an event


def midi_targets(mx, cursor):
    """{(section, row id): port ids} for app MIDI rows to watch: the selected one and every running one."""
    picks = [(n != cursor, n, (section, row.id), row.midi) for n, (kind, section, row) in enumerate(mixer_selectable(mx))
             if row.midi and (n == cursor or row.state == "running")]
    return {k: ports for _, _, k, ports in sorted(picks)[:MIDI_CAP]}


def _midi_dot(mx, key):
    """(text, spans) for a MIDI row where the volume bar goes: ● lit for a moment after an event, else ○."""
    seen, now = mx.get("midi_seen", {}).get(key), mx.get("now")
    if seen is not None and now is not None and now - seen <= MIDI_LIT:
        return "● MIDI", [(0, 1, "meter_low"), (2, 6, "normal")]
    return "○ MIDI", [(0, 6, "dim")]


def meter_level(prev, peak, dt, fall=20):
    """A peak meter's level: jumps up at once, then falls `fall` dB a second."""
    return max(peak, prev * 10 ** (-fall * dt / 20))


def held_peak(value, since, now, hold, fall=PEAK_FALL):
    """Where a peak marker stands now: at `value` for `hold` seconds after `since`, then falling."""
    late = now - since - hold
    return value if late <= 0 else value * 10 ** (-fall * late / 20)


def hold_peak(value, since, peak, now, hold):
    """(value, since) after a new peak: a peak at or over the marker restarts the hold."""
    return (peak, now) if peak >= held_peak(value, since, now, hold) else (value, since)


def _lit(level):
    """How many cells a level lights."""
    db = 20 * math.log10(level) if level and level > 0 else -_METER_DB
    return max(0, min(_METER, round((db + _METER_DB) / (_METER_DB / _METER))))


def _zone(cell):
    return "meter_low" if cell < 10 else "meter_mid" if cell < 14 else "meter_high"


def _meter_cells(level, hold=None):
    """(text, spans): lit cells green to -18 dB, yellow to -6 dB, red above; unlit cells dim.
    hold, when given, puts a marker (▌) in its zone's colour on the cell it reaches, above the bar."""
    n = _lit(level)
    text = list("█" * n + "·" * (_METER - n))
    spans = [(c, c + 1, _zone(c)) for c in range(n)] + [(n, _METER, "dim")]
    m = _lit(hold) - 1 if hold else -1
    if m >= n:
        text[m] = "▌"
        spans.append((m, m + 1, _zone(m)))
    return "".join(text), spans


_BAR = 24          # cells for 0–150%
_BAR_FULL = 16     # the cell where 100% ends


def _volume_bar(row, show_bar=True):
    """(text, spans relative to it): the volume % written over the bar's first cells, inverted against
    the cell under each character so it blends in; filled to 100% in ok, past it in alert, the rest dim.
    show_bar False leaves just the %."""
    if row.volume is None:
        text = "no volume control" if show_bar else "n/a"
        return text, [(0, len(text), "dim")]
    word = "muted" if row.mute else f"{round(row.volume * 100)}%"
    if not show_bar:
        return word, [(0, len(word), "dim")] if row.mute else []
    n = max(0, min(_BAR, round(row.volume / 1.5 * _BAR)))
    text = word + ("█" * n + "░" * (_BAR - n))[len(word):]
    if row.mute:
        spans = [(0, _BAR, "dim")]
    else:
        spans = [(0, min(n, _BAR_FULL), "vol")] + ([(_BAR_FULL, n, "vol_over")] if n > _BAR_FULL else []) + [(n, _BAR, "dim")]
    for col in range(len(word)):  # the overlay last, so it draws over the bar's colours
        role = "bar_dim" if row.mute or col >= n else "bar_alert" if col >= _BAR_FULL else "bar_ok"
        if spans[-1][2] == role and spans[-1][1] == col:
            spans[-1] = (spans[-1][0], col + 1, role)
        else:
            spans.append((col, col + 1, role))
    return text, spans


def _render_mixer(cv, st, mx, body_h):
    if mx is None:
        cv.add("  loading the mixer…", "dim")
        return
    items = mixer_selectable(mx)
    st.mix_cursor = max(0, min(len(items) - 1, st.mix_cursor)) if items else 0
    target = _mix_menu_target(mx, st)
    if target:
        st.mix_cursor = target[0]
    metered = mx.get("metered", {}) if st.meters else {}
    levels = mx.get("levels", {})
    vol_w = _BAR if st.volume_bar else 5  # bar off: just "100%" or "muted", and the meter moves in
    inner = max(10, cv.width - 4)  # each section is a box (Skrappjaw, 2026-10-26)
    label_w = max(20, min(44, inner - vol_w - 6 - (_METER + 2 if metered else 0)))
    out, n_sel, box, heading = [], 0, [], None

    def close():
        if heading:
            out.extend(_box_lines(heading, box, cv.width, "head_play" if heading == "Playback" else "head_rec"))
    for kind, section, row in _mixer_lines(mx):
        if kind == "head":
            close()
            box, heading = [], section
            continue
        if kind == "sub":
            box.append((f"  {section}", "title", None))  # a heading inside the box: title (ui-conventions)
            continue
        sel = n_sel == st.mix_cursor
        if kind == "dev":
            name = row.label
            star = "★" if _is_default(mx, section, row) else " "
        else:
            peers = row.outputs if section == "playback" else row.inputs
            name = f"{row.label} {'→' if section == 'playback' else '←'} {', '.join(peers) or 'nothing'}"
            star = " "
        indent = "  " if kind == "app" else ""
        w = label_w - len(indent)  # app rows sit under their section; the bars still line up
        head = f"{'> ' if sel else '  '}{star} {indent}{_fit(name, w):<{w}} "
        bar, spans = _midi_dot(mx, (section, row.id)) if row.media == "midi" else _volume_bar(row, st.volume_bar)
        bar = bar.ljust(vol_w)
        c0 = len(head)
        spans = [(c0 + a, c0 + b, r) for a, b, r in spans]
        if (section, row.id) in metered:
            level = levels.get((section, row.id), 0.0)
            cells, cell_spans = _meter_cells(*(level if isinstance(level, tuple) else (level,)))
            m0 = c0 + len(bar) + 2
            bar += "  " + cells
            spans += [(m0 + a, m0 + b, r) for a, b, r in cell_spans]
        side = "src" if section == "playback" else "dst"  # playback blue, recording orange (pipeOS)
        if sel:  # the cursor bar covers the name; the volume keeps its colours
            spans = [(0, c0 - 1, f"anchor_{side}" if target else f"bar_{side}")] + spans
        box.append((head + bar, ("normal", spans), n_sel))
        if sel and target:
            box += _menu_box(row.label, [label for label, _ in mix_menu_items(mx, st)], st.mix_menu_cursor,
                             f"bar_{side}", "head_play" if section == "playback" else "head_rec")
        n_sel += 1
    close()
    cursor_at = next((k for k, line in enumerate(out) if line[2] == st.mix_cursor), 0)
    if target:  # keep the whole box in view: scroll to its foot
        cursor_at = next(k for k in range(len(out) - 1, -1, -1) if isinstance(out[k][2], tuple)) + 1
    start = 0 if cursor_at < body_h else cursor_at - body_h + 1
    for text, style, idx in out[start:start + body_h]:
        row = cv.add(text, style)
        if isinstance(idx, tuple):
            cv.hit(row, 4, cv.width, ("mix_menu_item", idx[1]))
        elif idx is not None:
            cv.hit(row, 0, cv.width, ("mix_row", idx))


# --- Patch tab -------------------------------------------------------------------------

_STATE_MARK = {"full": "●", "partial": "◐", "none": "·", "n/a": " "}  # n/a: audio with MIDI


def patch_sources(g, st):
    """Monitors only with m, except one something records from (OBS's Desktop Audio): a live link never hides."""
    return [s for s in g.sources if st.patch_monitors or not s.monitor or g.targets(s)]


def _section(e):
    return "MIDI" if e.media == "midi" else "Monitors" if e.monitor else None


def _with_heads(items, indices):
    """The indices to draw, with a heading (a str) before the first monitor and the first MIDI endpoint;
    both sort after the rest, monitors first. A window that opens inside a section starts with its heading."""
    out = []
    for i in indices:
        sec = _section(items[i])
        prev = next((x for x in reversed(out) if not isinstance(x, str)), None)
        if sec and (prev is None or _section(items[prev]) != sec):
            out.append(sec)
        out.append(i)
    return out


def _n_heads(items):
    return len({_section(e) for e in items} - {None})


MIXED = "MIDI connects only to MIDI"


def _patch_pair(g, st):
    """(sources, source, destination) under the cursors, clamping them; None when nothing to pick."""
    srcs = patch_sources(g, st)
    if not srcs or not g.dests:
        return srcs, None, None
    st.patch_src = max(0, min(len(srcs) - 1, st.patch_src))
    st.patch_dst = max(0, min(len(g.dests) - 1, st.patch_dst))
    return srcs, srcs[st.patch_src], g.dests[st.patch_dst]


def _toggle(g, st, s, d):
    if s.media != d.media:
        st.message = st.failed = f"{s.label} → {d.label}: {MIXED}"
        return st, []
    cmds = pp.toggle_cmds(g, s, d)
    if not cmds:
        st.message = st.failed = f"nothing to link between {s.label} and {d.label}"
        return st, []
    return st, [("patch_cmds", cmds)]


def _connect(g, st, s, d):
    """Enter on a destination: a slot menu where there's a choice of inputs, else a toggle."""
    runs = pp.slots(s, d)
    if not runs:
        return _toggle(g, st, s, d)
    st.patch_menu, st.patch_menu_cursor = True, g.first_free(runs)
    return st, []


def _key_slot_menu(st, key, g, s, d):
    runs = pp.slots(s, d) if s else None
    st.patch_menu = False
    if not runs:
        return st, []
    st.patch_menu_cursor = max(0, min(len(runs) - 1, st.patch_menu_cursor))
    if key in ("up", "down"):
        st.patch_menu = True
        st.patch_menu_cursor = max(0, min(len(runs) - 1, st.patch_menu_cursor + (1 if key == "down" else -1)))
        return st, []
    if key != "enter":
        return st, []  # Esc, ← or any other key closes the menu, like the Devices menus
    return st, [("patch_cmds", pp.slot_cmds(g, s, runs[st.patch_menu_cursor]))]


def _key_patch(st, key, g):
    if g is None:
        return st, []
    if st.patch_menu:
        _, s, d = _patch_pair(g, st)
        return _key_slot_menu(st, key, g, s, d)
    if key == "g":
        st.patch_view = "grid" if st.patch_view == "cols" else "cols"
        st.patch_ports = False
        return st, []
    if key == "m":
        st.patch_monitors = not st.patch_monitors
        return st, []
    srcs, s, d = _patch_pair(g, st)
    if s is None:
        return st, []
    if st.patch_ports:
        st.patch_port_row = max(0, min(len(s.ports) - 1, st.patch_port_row))
        st.patch_port_col = max(0, min(len(d.ports) - 1, st.patch_port_col))
        if key in ("up", "down"):
            st.patch_port_row = max(0, min(len(s.ports) - 1, st.patch_port_row + (1 if key == "down" else -1)))
        elif key in ("left", "right"):
            st.patch_port_col = max(0, min(len(d.ports) - 1, st.patch_port_col + (1 if key == "right" else -1)))
        elif key == "enter" and s.media != d.media:
            st.message = st.failed = f"{s.label} → {d.label}: {MIXED}"
        elif key == "enter" and s.ports and d.ports:
            a, b = s.ports[st.patch_port_row], d.ports[st.patch_port_col]
            return st, [("patch_cmds", [pp.port_toggle_cmd(g, a, b)])]
        elif key in ("p", "esc"):
            st.patch_ports = False
        return st, []
    if key == "p":
        if st.patch_view == "cols" and st.patch_side == "src":
            # Ports belong to a pair the user chose: source, then destination, then port.
            _enter_dests(g, st, s)
            st.message = "choose a destination, then press p"
            return st, []
        st.patch_ports, st.patch_port_row, st.patch_port_col = True, 0, 0
        return st, []
    step = {"up": -1, "down": 1}.get(key)
    if st.patch_view == "grid":
        if step:
            st.patch_src = max(0, min(len(srcs) - 1, st.patch_src + step))
        elif key in ("left", "right"):
            st.patch_dst = max(0, min(len(g.dests) - 1, st.patch_dst + (1 if key == "right" else -1)))
        elif key == "enter":
            return _connect(g, st, s, d)
        return st, []
    if key == "left":
        st.patch_side = "src"
    elif key == "right" and st.patch_side == "src":
        _enter_dests(g, st, s)
    elif step and st.patch_side == "src":
        st.patch_src = max(0, min(len(srcs) - 1, st.patch_src + step))
    elif step:
        st.patch_dst = max(0, min(len(g.dests) - 1, st.patch_dst + step))
    elif key == "enter":
        if st.patch_side == "src":
            _enter_dests(g, st, s)
        else:
            return _connect(g, st, s, d)
    return st, []


def _enter_dests(g, st, s):
    """Move to the destination column, landing on something the source already feeds."""
    st.patch_side = "dst"
    targets = g.targets(s)
    if targets and g.dests[st.patch_dst] not in targets:
        st.patch_dst = g.dests.index(targets[0])
    elif not targets and g.dests[st.patch_dst].media != s.media:
        st.patch_dst = next((j for j, d in enumerate(g.dests) if d.media == s.media), st.patch_dst)


def _window(n, cursor, room):
    start = 0 if cursor < room else cursor - room + 1
    return range(start, min(n, start + room))


def _pair_status(g, s, d):
    state = g.state(s, d)
    if state == "n/a":
        return f"  {s.label} → {d.label}: {MIXED}"
    if state == "none":
        return f"  {s.label} → {d.label}: not connected"
    linked = ", ".join(f"{a.channel}→{b.channel}" for a, b, on in pp.port_pairs(g, s, d) if on)
    return f"  {s.label} → {d.label}: {'connected' if state == 'full' else 'partly connected'} ({linked})"


def _render_patch(cv, st, g, body_h):
    if g is None:
        cv.add("  loading the patch bay…", "dim")
        return
    srcs, s, d = _patch_pair(g, st)
    if s is None:
        cv.add("  no audio sources or destinations", "dim")
        return
    room = max(1, body_h - 2)  # blank line and pair status sit below
    # No View line (Skrappjaw, 2026-10-26: "View menu can go away ... patch bay shortcuts on screen are enough").
    if st.patch_ports:
        _render_port_matrix(cv, st, g, s, d, room)
    elif st.patch_view == "grid":
        _render_patch_grid(cv, st, g, srcs, s, d, room)
    else:
        _render_patch_cols(cv, st, g, srcs, s, room)
    cv.add("")
    if st.patch_view == "cols" and st.patch_side == "src" and not st.patch_ports:
        targets = g.targets(s)
        cv.add(f"  {s.label} → {', '.join(x.label for x in targets)}" if targets
               else f"  {s.label}: not connected", "alert" if targets else "dim")
    else:
        cv.add(_pair_status(g, s, d), "alert" if g.state(s, d) not in ("none", "n/a") else "dim")


def _slot_menu(g, st, s, d, avail):
    """The slot menu as box lines (text, role or None, hit target): each run of inputs and who feeds it."""
    runs = (pp.slots(s, d) or []) if st.patch_menu else []
    if not runs:
        return []
    st.patch_menu_cursor = max(0, min(len(runs) - 1, st.patch_menu_cursor))
    items = []
    for r in runs:
        a = d.ports.index(r[0]) + 1
        name = f"In {a}" if len(r) == 1 else f"In {a}-{a + len(r) - 1}"
        on = g.slot_sources(r)
        items.append(f"{'●' if s in on else ' '} {name:<8} {', '.join(x.label for x in on) or 'free'}")
    w = max(10, min(avail - 2, max([len(s.label) + 4] + [len(t) + 3 for t in items])))
    out = [("┌" + f"─ {_fit(s.label, w - 3)} ".ljust(w, "─") + "┐", "head_dst", None)]
    for k, text in enumerate(items):
        sel = k == st.patch_menu_cursor
        out.append(("│" + f"{'>' if sel else ' '} {_fit(text, w - 2)}".ljust(w) + "│", "bar_dst" if sel else None,
                    ("patch_slot", k)))
    out.append(("└" + "─" * w + "┘", "head_dst", None))
    return out


def _render_patch_cols(cv, st, g, srcs, s, room):
    half = max(20, (cv.width - 3) // 2)
    cv.add(f"  {'Sources':<{half - 2}}│ Destinations", ("title", [(2, 9, "head_src"), (half + 2, half + 14, "head_dst")]))
    on_dst = st.patch_side == "dst"
    menu = _slot_menu(g, st, s, g.dests[st.patch_dst], cv.width - half - 2) if on_dst else []
    left = _with_heads(srcs, list(_window(len(srcs), st.patch_src, room - 1 - _n_heads(srcs))))
    right = []  # (text, highlighted, hit target); the slot menu sits under its destination
    for j in _with_heads(g.dests, list(_window(len(g.dests), st.patch_dst,
                                               max(1, room - 1 - len(menu) - _n_heads(g.dests))))):
        if isinstance(j, str):
            right.append((f"  {j}", "title", None, None))
            continue
        mark = "> " if j == st.patch_dst and on_dst else "  "
        state = g.state(s, g.dests[j])
        rt = f"{mark}{_STATE_MARK[state].replace('·', ' ')} {_fit(g.dests[j].label, half - 4)}"
        lit = ("anchor_dst" if menu else "bar_dst") if j == st.patch_dst and on_dst else None
        right.append((rt, lit, ("patch_dst", j), {"full": "dst", "partial": "dst_partial"}.get(state)))
        if j == st.patch_dst:
            right += menu
    for n in range(max(len(left), len(right))):
        lt, spans = "", []
        if n < len(left) and isinstance(left[n], str):
            lt, spans = f"  {left[n]}", [(0, half, "title")]
        elif n < len(left):
            i = left[n]
            count = len(g.targets(srcs[i]))
            mark = "> " if i == st.patch_src and not on_dst else "* " if i == st.patch_src else "  "
            lt = f"{mark}{_fit(srcs[i].label, half - 7):<{half - 7}} {('●' + str(count)) if count else '':>3}"
            bar = ("anchor_src" if on_dst else "bar_src") if i == st.patch_src else None
            if bar:  # each bar covers only its own column; the source's stays as the anchor
                spans.append((0, half, bar))
            if count:  # the source's dot, drawn over its bar (src@bar_src) so it keeps its colour
                spans.append((lt.index("●"), lt.index("●") + 1, f"src@{bar}" if bar else "src"))
        rt, lit, target, dot = (right[n] + (None,))[:4] if n < len(right) else ("", None, None, None)
        if lit:
            spans.append((half + 2, cv.width if target and target[0] == "patch_dst" else half + 2 + len(rt), lit))
        if dot:
            spans.append((half + 2 + 2, half + 2 + 3, f"{dot}@{lit}" if lit in ("bar_dst", "anchor_dst") else dot))
        row = cv.add(f"{lt:<{half}}│ {rt}".ljust(cv.width), ("normal", spans))
        if n < len(left) and not isinstance(left[n], str):
            cv.hit(row, 0, half, ("patch_src", left[n]))
        if target:
            cv.hit(row, half + 1, cv.width, target)


def _render_patch_grid(cv, st, g, srcs, s, d, room):
    label_w = 30
    cols_fit = max(1, (cv.width - label_w - 3) // 3)
    cols = list(_window(len(g.dests), st.patch_dst, cols_fit))
    key_rows = -(-len(cols) // max(1, (cv.width - 2) // 35))
    menu = _slot_menu(g, st, s, d, cv.width - 4)
    rows = list(_window(len(srcs), st.patch_src, max(1, room - 2 - key_rows - len(menu) - _n_heads(srcs))))
    cv.add(" " * (label_w + 2) + "".join(f"{j + 1:>3}" for j in cols), "title")
    for i in _with_heads(srcs, rows):
        if isinstance(i, str):
            cv.add(f"  {i}", "title")
            continue
        cells = ""
        for j in cols:
            mark = _STATE_MARK[g.state(srcs[i], g.dests[j])]
            cells += f"[{mark}]" if (i, j) == (st.patch_src, st.patch_dst) else f" {mark} "
        sel = i == st.patch_src
        spans = []
        if sel:
            c0 = 2 + label_w + 3 * cols.index(st.patch_dst) if st.patch_dst in cols else None
            spans = [(0, 2 + label_w, "anchor_src")] + ([(c0, c0 + 3, "bar_dst")] if c0 is not None else [])
        row = cv.add(f"{'> ' if sel else '  '}{_fit(srcs[i].label, label_w):<{label_w}}{cells}", ("normal", spans))
        for k, j in enumerate(cols):
            c0 = 2 + label_w + 3 * k
            cv.hit(row, c0, c0 + 3, ("patch_cell", i, j))
    for text, lit, target in menu:
        row = cv.add("    " + text, ("normal", [(4, 4 + len(text), lit)] if lit else []))
        if target:
            cv.hit(row, 4, 4 + len(text), target)
    cv.add("")
    per_row = max(1, (cv.width - 2) // 35)  # as many "nn name" columns as fit the screen
    for start in range(0, len(cols), per_row):
        cv.add("  " + "".join(f"{j + 1:>3} {_fit(g.dests[j].label, 30):<31}" for j in cols[start:start + per_row]), "dim")


def _render_port_matrix(cv, st, g, s, d, room):
    """Source ports down the side, destination ports across the top; one cell per possible link."""
    cv.add(f"  Ports: {s.label} → {d.label}", "title")
    st.patch_port_row = max(0, min(len(s.ports) - 1, st.patch_port_row))
    st.patch_port_col = max(0, min(len(d.ports) - 1, st.patch_port_col))
    row_w = max(4, max((len(p.channel) for p in s.ports), default=0)) + 2
    cell_w = max(5, max((len(p.channel) for p in d.ports), default=0) + 2)
    cols = list(_window(len(d.ports), st.patch_port_col, max(1, (cv.width - 2 - row_w) // cell_w)))
    cv.add(" " * (2 + row_w) + "".join(d.ports[j].channel.center(cell_w) for j in cols), "title")
    for i in _window(len(s.ports), st.patch_port_row, max(1, room - 2)):
        cells = ""
        for j in cols:
            mark = "●" if (s.ports[i].id, d.ports[j].id) in g.links else "·"
            cursor = (i, j) == (st.patch_port_row, st.patch_port_col)
            cells += (f"[{mark}]" if cursor else f" {mark} ").center(cell_w)
        sel = i == st.patch_port_row
        spans = []
        if sel:
            c0 = 2 + row_w + cell_w * cols.index(st.patch_port_col) if st.patch_port_col in cols else None
            spans = [(0, 2 + row_w, "anchor_src")] + ([(c0, c0 + cell_w, "bar_dst")] if c0 is not None else [])
        row = cv.add(f"{'> ' if sel else '  '}{s.ports[i].channel:<{row_w}}{cells}", ("normal", spans))
        for k, j in enumerate(cols):
            c0 = 2 + row_w + cell_w * k
            cv.hit(row, c0, c0 + cell_w, ("patch_pcell", i, j))


# --- Devices tab: Enter-driven action menus ------------------------------------------------

def _menu_target(devices, st):
    """The (kind, obj) the open menu belongs to, or None if it has gone away."""
    if not st.dev_menu:
        return None
    kind, ref = st.dev_menu
    for k, obj in dev_selectable(devices, st):
        if k == kind == "row" and obj.name == ref:
            return k, obj
        if k == kind == "card" and obj.id == ref:
            return k, obj
        if k == kind == "pending" and obj["name"] == ref:
            return k, obj
    return None


def dev_menu_items(devices, st):
    """[(label, op)] for the open menu."""
    target = _menu_target(devices, st)
    if target is None:
        return []
    kind, obj = target
    hidden = set(devices["config"]["hidden"])
    if kind == "card":
        items = [(f"{'●' if p.name == obj.active else '○'} {p.name}", ("profile", p.index, p.name)) for p in obj.profiles]
        items.append(("Unhide card" if obj.name in hidden else "Hide card", ("hide", obj.name)))
        # Channels can't be hidden one by one any more; older configs may still hide some.
        hidden_chans = tuple(r.name for r in obj.rows if r.name in hidden)
        if hidden_chans:
            n = len(hidden_chans)
            items.append((f"Unhide {n} channel{'s' if n > 1 else ''}", ("unhide", hidden_chans)))
        return items + [("Back", ("back",))]
    if kind == "pending":
        return [("Create now", ("create", obj["name"])), ("Rename…", ("rename", obj["name"], obj["label"])),
                ("Delete bus", ("delete", obj["name"])), ("Back", ("back",))]
    # A bus (the only row with a cursor): default, rename and test live on the Mixer.
    test = [("Test noise", ("test", obj.name))] if obj.direction in ("out", "duplex") else []
    return test + [("Unhide" if obj.name in hidden else "Hide", ("hide", obj.name)),
                   ("Delete bus", ("delete", obj.name)), ("Back", ("back",))]


def _open_menu(st, kind, obj):
    if kind == "card":
        st.dev_menu = ("card", obj.id)
        names = [p.name for p in obj.profiles]
        st.dev_menu_cursor = names.index(obj.active) if obj.active in names else 0
    else:
        st.dev_menu = (kind, obj.name if kind == "row" else obj["name"])
        st.dev_menu_cursor = 0


def focus_device(st, devices):
    """Resolve dev_focus once Devices has loaded: put the cursor on that card and open its menu."""
    if not st.dev_focus or devices is None:
        return st
    (_, card_id), st = st.dev_focus, dataclasses.replace(st, dev_focus=None)
    for i, (kind, obj) in enumerate(dev_selectable(devices, st)):
        if kind == "card" and obj.id == card_id:
            st.dev_cursor = i
            _open_menu(st, kind, obj)
            break
    return st


def _key_menu(st, key, devices):
    items = dev_menu_items(devices, st)
    if not items:
        st.dev_menu = None
        return st, []
    st.dev_menu_cursor = max(0, min(len(items) - 1, st.dev_menu_cursor))
    if key in ("up", "down"):
        st.dev_menu_cursor = max(0, min(len(items) - 1, st.dev_menu_cursor + (1 if key == "down" else -1)))
        return st, []
    target = _menu_target(devices, st)
    st.dev_menu = None
    if key != "enter":
        return st, []  # Esc, ← or any other key closes the menu, like a cancelled prompt
    op = items[st.dev_menu_cursor][1]
    obj = target[1]
    if op[0] == "rename":
        return st, [("dev_rename", op[1], op[2])]
    if op[0] == "default":
        try:
            return st, [("dev_cmd", pi.default_cmd(obj))]
        except ValueError as e:
            st.message = str(e)
            return st, []
    if op[0] == "test":
        return st, [("dev_test", op[1])]
    if op[0] == "hide":
        return st, [("dev_hide", op[1])]
    if op[0] == "unhide":
        return st, [("dev_hide", name) for name in op[1]]  # each is hidden, so the toggle shows it
    if op[0] == "delete":
        st.popup = ("dev_delete_bus", op[1])
    elif op[0] == "create":
        return st, [("dev_bus_create", op[1])]
    elif op[0] == "profile" and op[2] != obj.active:
        st.popup = ("dev_profile", obj.id, op[1], op[2])
    return st, []


def _menu_lines(devices, st, title):
    return _menu_box(title, [label for label, _ in dev_menu_items(devices, st)], st.dev_menu_cursor)


def _menu_box(title, labels, cursor, bar="selected", edge="title"):
    """A box under the selected row (Devices and Mixer). Every line shares one interior width, so the
    edges line up. Item lines carry ("menu", k) for their click target. bar and edge are the row's direction
    (bar_src/head_play, bar_dst/head_rec); a profile's ●/○ is a choice, not a connection, so it stays plain."""
    width = max(26, len(title) + 4, max((len(label) for label in labels), default=0) + 5)
    lines = [("    ┌" + f"─ {title} ".ljust(width, "─") + "┐", edge, None)]
    for k, label in enumerate(labels):
        sel = k == cursor
        role = bar if sel else "normal"
        sides = [(4, 5, edge), (5 + width, 6 + width, edge)]  # the box's edges, in the edge colour
        lines.append(("    │" + f" {'> ' if sel else '  '}{label}".ljust(width) + "│",
                      ("normal", [(5, 5 + width, role)] + sides + ([(8, 9, role)] if label[:1] in "●○" else [])),
                      ("menu", k)))  # the bar stays inside the box
    lines.append(("    └" + "─" * width + "┘", edge, None))
    return lines
