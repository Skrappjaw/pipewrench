"""Pure logic for PipeWrench: PipeWire settings metadata, pw-top parsing, presets, and the TUI
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
import pwmedia as pm
import pwpatch as pp
import pwrealtime as prt

READ_CMD = ["pw-metadata", "-n", "settings", "0"]
RESTART_CMD = ["systemctl", "--user", "restart", "wireplumber"]

# Colour roles (notes/ui-conventions.md) as (fg, bg, attr) per theme; None is the screen's own colour.
# One bar on screen is ever "selected": that's where the keys go. "anchor" is selected but not active.
# Never bold on black text: terminals draw bold black as grey.
THEMES = {
    "dark": {"screen": (None, None, ""),
             "normal": (None, None, ""), "dim": (None, None, "dim"),
             "selected": ("black", "cyan", ""), "anchor": ("white", "blue", ""),
             "title": ("cyan", None, "bold"),
             "ok": ("green", None, "bold"), "pending": ("yellow", None, "bold"), "alert": ("red", None, "bold"),
             "chip": ("black", "white", ""),
             "bar_ok": ("black", "green", ""), "bar_alert": ("black", "red", ""), "bar_dim": (None, None, "reverse dim")},
    "light": {"screen": ("black", "brightwhite", ""),
              "normal": (None, None, ""), "dim": (None, None, "dim"),
              "selected": ("black", "cyan", ""), "anchor": ("white", "blue", ""),
              "title": ("blue", None, "bold"),
              "ok": ("green", None, "bold"), "pending": ("magenta", None, "bold"), "alert": ("red", None, "bold"),
              "chip": ("white", "black", "bold"),
              "bar_ok": ("white", "green", ""), "bar_alert": ("white", "red", ""), "bar_dim": (None, None, "reverse dim")},
    # Crowbar: Linux Mint's Mint-Y-Dark-Orange (gtk-3.0 colours); exact hex where the terminal allows.
    "crowbar": {"screen": ("#dedee0", "#2e2e33", ""),
               "normal": (None, None, ""), "dim": (None, None, "dim"),
               "selected": ("#000000", "#ff7139", ""), "anchor": ("#ff7139", "#45454b", ""),
               "title": ("#ff7139", None, "bold"),
               "ok": ("#73d216", None, "bold"), "pending": ("#f6d32d", None, "bold"), "alert": ("#fc4138", None, "bold"),
               "chip": ("#2e2e33", "#dedee0", ""),
               "bar_ok": ("#2e2e33", "#73d216", ""), "bar_alert": ("#2e2e33", "#fc4138", ""),
               "bar_dim": (None, None, "reverse dim")},
    # Vaporware: vaporwave colours on dark purple.
    "vaporware": {"screen": ("#e8d9ff", "#1b0d33", ""),
                  "normal": (None, None, ""), "dim": ("#8a6fb3", None, ""),
                  "selected": ("#1b0d33", "#01cdfe", ""), "anchor": ("#01cdfe", "#3a1d5c", ""),
                  "title": ("#ff71ce", None, "bold"),
                  "ok": ("#05ffa1", None, "bold"), "pending": ("#fffb96", None, "bold"), "alert": ("#ff4f81", None, "bold"),
                  "chip": ("#1b0d33", "#b967ff", ""),
                  "bar_ok": ("#1b0d33", "#05ffa1", ""), "bar_alert": ("#1b0d33", "#ff4f81", ""),
                  "bar_dim": ("#8a6fb3", "#3a1d5c", "")},
    # Pipecraft: Minecraft's text-colour codes (yellow, green, gold, red, grey) on dark dirt, grey buttons.
    "pipecraft": {"screen": ("#ffffff", "#2b1d12", ""),
                  "normal": (None, None, ""), "dim": ("#aaaaaa", None, ""),
                  "selected": ("#ffffa0", "#6f6f6f", "bold"), "anchor": ("#ffffff", "#4a4a4a", ""),
                  "title": ("#ffff55", None, "bold"),
                  "ok": ("#55ff55", None, "bold"), "pending": ("#ffaa00", None, "bold"), "alert": ("#ff5555", None, "bold"),
                  "chip": ("#ffffff", "#555555", ""),
                  "bar_ok": ("#ffffff", "#5d9632", ""), "bar_alert": ("#ffffff", "#aa0000", ""),
                  "bar_dim": ("#aaaaaa", "#4a3624", "")},
    # Portal: Patch's source dots are the blue portal, destination dots the orange one.
    # Light: a clean test chamber, white panels and grey trim.
    "portal-light": {"screen": ("#2b2f33", "#f2f4f5", ""),
                     "normal": (None, None, ""), "dim": ("#8a9399", None, ""),
                     "selected": ("#ffffff", "#4f5b63", ""), "anchor": ("#2b2f33", "#d5dadd", ""),
                     "title": ("#1b1e21", None, "bold"),
                     "ok": ("#2f9e44", None, "bold"), "pending": ("#8a6d00", None, "bold"),
                     "alert": ("#d62828", None, "bold"),
                     "chip": ("#ffffff", "#2b2f33", ""),
                     "bar_ok": ("#ffffff", "#6c7a84", ""), "bar_alert": ("#ffffff", "#d62828", ""),
                     "bar_dim": ("#8a9399", "#e1e5e8", ""),
                     "src": ("#0a84ff", None, "bold"), "dst": ("#ff7a00", None, "bold"),
                     "dst_partial": ("#ff7a00", None, "")},
    # Dark: Portal 2's moody chambers, dark panels with the portals glowing.
    "portal-dark": {"screen": ("#c9d1d6", "#14181b", ""),
                    "normal": (None, None, ""), "dim": ("#5f6b73", None, ""),
                    "selected": ("#e8eef2", "#2f4250", ""), "anchor": ("#c9d1d6", "#222a30", ""),
                    "title": ("#e8eef2", None, "bold"),
                    "ok": ("#5fbf6a", None, "bold"), "pending": ("#d8c35a", None, "bold"),
                    "alert": ("#e5484d", None, "bold"),
                    "chip": ("#e8eef2", "#2f4250", ""),
                    "bar_ok": ("#e8eef2", "#3d5566", ""), "bar_alert": ("#ffffff", "#b3261e", ""),
                    "bar_dim": ("#5f6b73", "#1d2327", ""),
                    "src": ("#28a7ff", None, "bold"), "dst": ("#ff9a1f", None, "bold"),
                    "dst_partial": ("#ff9a1f", None, "")},
}


def _pipe_boy(text, pale, mid, deep, back):
    """Pipe Boy, after Fallout's Pip-Boy: one phosphor colour at five brightnesses. Without hue, status reads by
    style: ok bold, pending underlined, alert a pale block."""
    return {"screen": (text, back, ""),
            "normal": (None, None, ""), "dim": (mid, None, ""),
            "selected": (back, text, ""), "anchor": (text, deep, ""),
            "title": (pale, None, "bold"),
            "ok": (text, None, "bold"), "pending": (text, None, "underline"), "alert": (back, pale, ""),
            "chip": (pale, deep, "bold"),
            "bar_ok": (back, mid, ""), "bar_alert": (back, pale, ""), "bar_dim": (mid, deep, "")}


# The Pipe Boys sit after crowbar and before vaporware in the cycle.
_PIPE_BOYS = {"pipeboy": ("#3cff62", "#b6ffc2", "#1f8a35", "#13401c", "#0b1a0d"),        # green
              "pipeboy-amber": ("#ffb000", "#ffe0a0", "#9a6a00", "#4a3300", "#1a1000"),
              "pipeboy-cyan": ("#33f0ff", "#b8f9ff", "#1a8a94", "#0d3f45", "#071a1c"),
              "pipeboy-white": ("#e6e6e6", "#ffffff", "#8c8c8c", "#3a3a3a", "#121212")}
THEMES = {**{k: v for k, v in THEMES.items() if k not in ("vaporware", "pipecraft", "portal-light", "portal-dark")},
          **{name: _pipe_boy(*colours) for name, colours in _PIPE_BOYS.items()},
          "vaporware": THEMES["vaporware"], "pipecraft": THEMES["pipecraft"],
          "portal-light": THEMES["portal-light"], "portal-dark": THEMES["portal-dark"]}
for _t in THEMES.values():  # Patch's dots: the theme's ok and pending, unless it sets its own (Portal)
    _t.setdefault("src", _t["ok"])
    _t.setdefault("dst", _t["ok"])
    _t.setdefault("dst_partial", _t["pending"])
THEME_NAMES = list(THEMES)
THEME_LABELS = {"pipeboy": "Pipe Boy", "pipeboy-amber": "Pipe Boy Amber", "pipeboy-cyan": "Pipe Boy Cyan",
                "pipeboy-white": "Pipe Boy White", "vaporware": "Vaporware", "pipecraft": "Pipecraft",
                "portal-light": "Portal Light", "portal-dark": "Portal Dark"}


def theme_label(name):
    return THEME_LABELS.get(name, name)

METER_FALL = [10, 20, 40, 80]  # dB a second: slow, VU-like, through fast
PEAK_HOLD = [0, 0.5, 1, 1.5, 2, 3]  # seconds a peak marker hangs before it falls; 0 is off
PEAK_FALL = 20  # dB a second, once the hold is over
METER_FPS = [5, 10, 15, 20, 30, 45, 60]  # new audio lands once per graph cycle (21 ms at 1024/48k); falls redraw every frame
THEME_MIN_COLOURS = {name: 256 for name in THEME_NAMES if name not in ("dark", "light")}  # exact hex  # below this the theme falls back to dark


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
    theme = {"orange": "crowbar"}.get(theme, theme)  # renamed 2026-10-24
    ui = ui if isinstance(ui, dict) else {}
    return {"theme": theme if theme in THEMES else "dark",
            "meter_fps": ui.get("meter_fps") if ui.get("meter_fps") in METER_FPS else 10,
            "meter_fall": ui.get("meter_fall") if ui.get("meter_fall") in METER_FALL else 20,
            "peak_hold": ui.get("peak_hold") if ui.get("peak_hold") in PEAK_HOLD else 1.5,
            "volume_bar": ui.get("volume_bar") if isinstance(ui.get("volume_bar"), bool) else True,
            "keys": pk.clean(ui.get("keys"))}


def ui_prefs(st):
    """What ui.json keeps, from the live state."""
    return {"theme": st.theme, "meter_fps": st.meter_fps, "meter_fall": st.meter_fall, "peak_hold": st.peak_hold,
            "volume_bar": st.volume_bar, "keys": pk.overrides(pk.bindings(st.keys))}


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

SETTINGS = [
    Setting("clock.force-rate", "Force sample rate", "force rate",
            [0, 44100, 48000, 88200, 96000, 176400, 192000]),
    Setting("clock.min-quantum", "Minimum buffer", "min buffer", _BUFFERS),
    Setting("clock.max-quantum", "Max buffer", "max buffer", _BUFFERS),
    Setting("clock.force-quantum", "Force buffer size", "force buffer", [0] + _BUFFERS),
    Setting("log.level", "Log level", "log level", list(range(6))),
]
KEYS = [s.key for s in SETTINGS]
FORCE_KEYS = ("clock.force-rate", "clock.force-quantum")
CAPTURE_KEYS = ("clock.force-rate", "clock.force-quantum", "clock.min-quantum", "clock.max-quantum")


def setting(key):
    for s in SETTINGS:
        if s.key == key:
            return s
    raise KeyError(key)


def display_value(s, value):
    if value is None:
        return "?"
    if s.key in FORCE_KEYS and value == 0:
        return "do not force"
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


def validate(current, changes):
    """Raise ValueError for impossible combinations; return warnings for ones PipeWire will adjust."""
    merged = {**current, **changes}
    lo, hi = merged.get("clock.min-quantum"), merged.get("clock.max-quantum")
    if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
        raise ValueError(f"min buffer {lo} is larger than max buffer {hi}")
    warnings = []
    fq = merged.get("clock.force-quantum")
    if fq and isinstance(lo, int) and isinstance(hi, int) and not lo <= fq <= hi:
        warnings.append(f"force buffer {fq} is outside min/max {lo}-{hi}; PipeWire will clamp it")
    fr = merged.get("clock.force-rate")
    allowed = merged.get("clock.allowed-rates")
    if fr and isinstance(allowed, list) and allowed and fr not in allowed:
        rates = ", ".join(map(str, allowed))
        warnings.append(f"rate {fr} is not in allowed rates ({rates}); the device may not switch")
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


# --- presets -----------------------------------------------------------------

_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")


def presets_path():
    if os.environ.get("PIPEWRENCH_PRESETS"):
        return Path(os.environ["PIPEWRENCH_PRESETS"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "pipewrench" / "presets.json"


def validate_preset(p):
    if not isinstance(p.get("name"), str) or not _NAME.fullmatch(p["name"]):
        raise ValueError("preset name must be 1-32 letters, digits, - or _")
    if not isinstance(p.get("label"), str):
        raise ValueError("preset label must be text")
    values = p.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("preset needs at least one value")
    for k, v in values.items():
        _check_value(k, v)
    validate({}, values)


def load_presets(path):
    path = Path(path)
    if not path.exists():
        return []
    presets = json.loads(path.read_text()).get("presets", [])
    for p in presets:
        validate_preset(p)
    return presets


def save_presets(path, presets):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"presets": presets}, indent=2) + "\n")
    os.replace(tmp, path)


def find_preset(presets, name):
    return next((p for p in presets if p["name"] == name), None)


def add_preset(presets, p):
    validate_preset(p)
    if find_preset(presets, p["name"]):
        raise ValueError(f"a preset named {p['name']!r} already exists")
    return presets + [json.loads(json.dumps(p))]


def delete_preset(presets, name):
    if not find_preset(presets, name):
        raise KeyError(name)
    return [p for p in presets if p["name"] != name]


def relabel_preset(presets, name, label):
    if not find_preset(presets, name):
        raise KeyError(name)
    return [{**p, "label": label} if p["name"] == name else p for p in presets]


def capture_preset(current, name, label):
    return {"name": name, "label": label,
            "values": {k: current[k] for k in CAPTURE_KEYS if isinstance(current.get(k), int)}}


def preset_cmds(p, current):
    return [set_cmd(k, v) for k, v in ordered_changes(current, p["values"])]


def summarize_preset(p):
    v = p["values"]
    parts = []
    for key, word in (("clock.force-rate", "rate"), ("clock.force-quantum", "buf"),
                      ("clock.min-quantum", "min"), ("clock.max-quantum", "max")):
        if key in v:
            parts.append(f"{word} {'auto' if key in FORCE_KEYS and v[key] == 0 else v[key]}")
    if "log.level" in v:
        parts.append(f"log {v['log.level']}")
    return " · ".join(parts)


# --- TUI state machine -------------------------------------------------------

# Basic first (everyday audio), then the engineering tools. Presets live inside Settings. About is last.
TABS = ["mixer", "devices", "patch", "settings", "top", "about"]
TAB_TITLES = {t: f"{n} {t.capitalize()}" for n, t in enumerate(TABS, start=1)}


@dataclass
class UIState:
    tab: str = "mixer"
    mix_cursor: int = 0
    mix_menu: tuple = None       # (section, node id) of the row whose menu is open
    mix_menu_cursor: int = 0
    meters: bool = True          # live levels on the Mixer (v toggles)
    cursor: int = 0
    pending: dict = field(default_factory=dict)
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
    confirm: tuple = None
    message: str = ""
    failed: str = ""             # the message is drawn red while it still equals this
    theme: str = "dark"
    meter_fps: int = 10
    meter_fall: int = 20         # dB a second
    peak_hold: float = 1.5       # seconds; 0 is off
    volume_bar: bool = True      # the bar under each Mixer channel; off leaves just the %
    keys: dict = field(default_factory=dict)  # key binding overrides {action id: key or None} (pwkeys)
    key_capture: str = None      # Settings → Keys: the action id waiting for its new key


# The now-playing bar's keys work on every tab; no tab uses these characters. * - + sit on the numpad.
# No stop: Brave ends its media session on Stop, and only the browser can start it again.
MEDIA_KEYS = {"*": ("media", "play/pause"), "<": ("media", "previous"),
              ">": ("media", "next"), "-": ("media_volume", "-5%"), "+": ("media_volume", "+5%"),
              "=": ("media_volume", "+5%")}


def handle_key(st, key, current, presets, devices=None, patch=None, mixer=None):
    """Return (new_state, actions). Nothing touches PipeWire until an action is executed.

    devices is the Devices tab's view: {"model", "status", "buses", "dirty"}, or None until loaded.
    """
    st = dataclasses.replace(st, pending=dict(st.pending))
    if st.key_capture:
        return _capture_key(st, key)
    if st.confirm:
        if key == "y":
            kind = st.confirm
            st.confirm = None
            if kind == ("reset_keys",):
                st.keys, st.message = {}, "keys reset to the defaults"
                return st, [("save_ui",)]
            # confirm tuples name their action, except the two v1 ones
            return st, [{"restart": ("restart_wireplumber",), "restart_labels": ("restart_wireplumber",),
                         "delete": ("delete_preset", *kind[1:])}.get(kind[0], kind)]
        # Any other key cancels: a missed prompt that swallowed keys looked like a frozen screen.
        st.confirm, st.message = None, "cancelled" if key in ("n", "esc") else "cancelled (y confirms)"
        return st, []
    if key == "q":
        return st, [("quit",)]
    if key == "tab":
        st.tab = TABS[(TABS.index(st.tab) + 1) % len(TABS)]
        return st, []
    if key.isdigit() and 1 <= int(key) <= len(TABS):
        st.tab = TABS[int(key) - 1]
        return st, []
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
    return {"settings": _key_settings, "top": _key_top}[st.tab](st, key, current, presets)


# Settings is one list: the clock rows, then Display (PipeWrench's own rows; they apply at once), then presets.
THEME_ROW = len(SETTINGS)
FPS_ROW = THEME_ROW + 1
FALL_ROW = FPS_ROW + 1
HOLD_ROW = FALL_ROW + 1
BAR_ROW = HOLD_ROW + 1
KEYS_ROW = BAR_ROW + 1     # one row per pwkeys action
RESET_KEYS_ROW = KEYS_ROW + len(pk.ACTIONS)
PRESETS_ROW = RESET_KEYS_ROW + 1  # the first preset


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


def _key_settings(st, key, current, presets):
    last = PRESETS_ROW + len(presets) - 1
    st.cursor = max(0, min(last, st.cursor))
    if key == "s":
        return st, [("prompt_save",)]
    if KEYS_ROW <= st.cursor < RESET_KEYS_ROW and key in ("enter", "backspace", "delete", "left", "right"):
        a = pk.ACTIONS[st.cursor - KEYS_ROW]
        if key == "enter":
            st.key_capture = a.id
            st.message = f"press a key for {pk.label(a)} (Esc cancels, Backspace unbinds)"
            return st, []
        return _bind(st, a.id, None) if key in ("backspace", "delete") else (st, [])
    if st.cursor == RESET_KEYS_ROW and key in ("enter", "left", "right"):
        if key == "enter":
            st.confirm = ("reset_keys",)
        return st, []
    if st.cursor >= PRESETS_ROW and key in ("enter", "e", "d", "left", "right"):
        return _key_preset_row(st, key, presets[st.cursor - PRESETS_ROW]["name"])
    if key in ("left", "right") and st.cursor == BAR_ROW:
        st.volume_bar = not st.volume_bar
        return st, [("save_ui",)]
    if key in ("left", "right") and st.cursor in (FPS_ROW, FALL_ROW, HOLD_ROW):
        name, choices = {FPS_ROW: ("meter_fps", METER_FPS), FALL_ROW: ("meter_fall", METER_FALL),
                         HOLD_ROW: ("peak_hold", PEAK_HOLD)}[st.cursor]
        n = max(0, min(len(choices) - 1, choices.index(getattr(st, name)) + (1 if key == "right" else -1)))
        setattr(st, name, choices[n])
        return st, [("save_ui",)]
    if key in ("up", "down"):
        st.cursor = max(0, min(last, st.cursor + (1 if key == "down" else -1)))
    elif key in ("left", "right") and st.cursor == THEME_ROW:
        st.theme = THEME_NAMES[(THEME_NAMES.index(st.theme) + (1 if key == "right" else -1)) % len(THEME_NAMES)]
        return st, [("set_theme", st.theme)]
    elif key in ("left", "right"):
        s = SETTINGS[st.cursor]
        base = st.pending.get(s.key, current.get(s.key))
        new = cycle(s, base if isinstance(base, int) else s.choices[0], 1 if key == "right" else -1)
        if new == current.get(s.key):
            st.pending.pop(s.key, None)
        else:
            st.pending[s.key] = new
        st.message = ""
    elif key == "enter":
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
    elif key == "esc":
        if st.pending:
            st.pending, st.message = {}, "reverted"
    elif key == "R":
        st.confirm = ("restart",)
    return st, []


def _key_top(st, key, current, presets):
    step = {"down": 1, "up": -1, "pgdn": 10, "pgup": -10}.get(key)
    if step:
        st.top_scroll = max(0, st.top_scroll + step)
    return st, []


def _key_preset_row(st, key, name):
    if key == "enter":
        return st, [("apply_preset", name)]
    elif key == "e":
        return st, [("prompt_label", name)]
    elif key == "d":
        st.confirm = ("delete", name)
    return st, []


def devices_view(inv, config, dirty=False, owned=(), clash=(), pending=()):
    """Everything the Devices tab draws: the live inventory, devices.json, whether the generated
    files are behind it, and bus status (PipeWrench-owned, clashing with a bus made elsewhere, pending)."""
    return {"inv": inv, "config": config, "dirty": dirty, "owned": set(owned), "clash": set(clash),
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
    lines.append(("virtual", None))
    lines += rows(inv.virtual)
    lines += [("pending", b) for b in devices["pending"]]
    lines.append(("addbus", None))
    return lines + ([("off", None)] + off if off else [])


def dev_selectable(devices, st):
    return [line for line in _dev_lines(devices, st) if line[0] not in ("virtual", "off", "chan")]


def _key_devices(st, key, devices):
    if devices is None:
        return st, []
    if st.dev_menu:
        return _key_menu(st, key, devices)
    if key == "a":
        return st, [("dev_prompt_bus",)]
    if key == "v":
        return st, [("dev_preview",)]
    if key == "A":
        return st, [("dev_apply",)]
    if key == "R":
        st.confirm = ("restart",)
        return st, []
    if key == "h":  # show hidden; nothing hides on a key (a stray h hid the GCS-8, Skrappjaw 2026-10-24)
        st.dev_show_hidden = not st.dev_show_hidden
        return st, []
    if key == "m":
        st.dev_monitors = not st.dev_monitors
        return st, []
    items = dev_selectable(devices, st)
    if not items:
        return st, []
    st.dev_cursor = max(0, min(len(items) - 1, st.dev_cursor))
    kind, obj = items[st.dev_cursor]
    if key in ("up", "down"):
        st.dev_cursor = max(0, min(len(items) - 1, st.dev_cursor + (1 if key == "down" else -1)))
    elif key in ("left", "right", " "):  # pointers for old habits: levels live on the Mixer only
        st.message = "volume and mute are on the Mixer (1)"
    elif key == "d":
        st.message = "default and rename are on the Mixer (1)"
    elif key == "enter":
        if kind == "addbus":
            return st, [("dev_prompt_bus",)]
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
            st.confirm = ("dev_delete_bus", obj["name"] if kind == "pending" else obj.name)
        else:
            st.message = st.failed = "x deletes a virtual bus"
    elif key == "t":
        if kind == "row" and obj.kind != "monitor" and obj.direction in ("out", "duplex"):
            return st, [("dev_test", obj.name)]
        st.message = st.failed = "t plays a test noise on an output"
    return st, []


def handle_mouse(st, hits, y, x, event, current, presets, devices=None, patch=None, mixer=None):
    """Map a mouse event onto the same keys the keyboard uses."""
    if st.confirm:
        return dataclasses.replace(st, confirm=None, message="cancelled (y confirms)"), []
    target = next((t for row, c0, c1, t in hits if row == y and c0 <= x < c1), None)
    if event in ("wheel_up", "wheel_down"):
        # The wheel scrolls everywhere (Skrappjaw, 2026-10-24): ↑/↓ on the list in focus, wherever the mouse is,
        # and never changes a value. On Patch it scrolls the column it's over.
        if target and target[0] in ("patch_src", "patch_dst") and not st.patch_menu:
            st = dataclasses.replace(st, patch_side="src" if target[0] == "patch_src" else "dst")
        return handle_key(st, "up" if event == "wheel_up" else "down", current, presets, devices, patch, mixer)
    if target is None:
        return st, []
    kind = target[0]
    keys = []
    if kind == "tab":
        return dataclasses.replace(st, tab=target[1]), []
    if kind == "setting":
        st = dataclasses.replace(st, cursor=target[1])
        keys = ["enter"] if event == "double" else []
    elif kind == "arrow":
        st = dataclasses.replace(st, cursor=target[1])
        keys = ["right" if target[2] > 0 else "left"] if event in ("click", "double") else []
    elif kind == "key":  # a legend chip
        keys = [target[1]] if event in ("click", "double") else []
    elif kind == "preset":
        st = dataclasses.replace(st, cursor=PRESETS_ROW + target[1])
        keys = ["enter"] if event == "double" else []
    elif kind == "mix_row":
        st = dataclasses.replace(st, mix_cursor=target[1], mix_menu=None)
        keys = ["enter"] if event == "double" else []
    elif kind == "mix_menu_item":
        st = dataclasses.replace(st, mix_menu_cursor=target[1])
        keys = ["enter"] if event in ("click", "double") else []
    elif kind == "dev_menu_item":
        st = dataclasses.replace(st, dev_menu_cursor=target[1])
        keys = ["enter"] if event in ("click", "double") else []
    elif kind == "device":
        st = dataclasses.replace(st, dev_cursor=target[1], dev_menu=None)
        keys = ["enter"] if event == "double" else []
    elif kind in ("patch_src", "patch_dst"):
        side = "src" if kind == "patch_src" else "dst"
        st = dataclasses.replace(st, patch_side=side, patch_menu=False, **{f"patch_{side}": target[1]})
        keys = ["enter"] if event == "double" and side == "dst" else []
    elif kind == "patch_cell":
        st = dataclasses.replace(st, patch_src=target[1], patch_dst=target[2], patch_menu=False)
        keys = ["enter"] if event == "double" else []
    elif kind == "patch_slot":
        st = dataclasses.replace(st, patch_menu_cursor=target[1])
        keys = ["enter"] if event in ("click", "double") else []
    elif kind == "patch_pcell":
        st = dataclasses.replace(st, patch_port_row=target[1], patch_port_col=target[2])
        keys = ["enter"] if event == "double" else []
    actions = []
    for k in keys:
        st, a = handle_key(st, k, current, presets, devices, patch, mixer)
        actions += a
    return st, actions


# --- rendering (view model) --------------------------------------------------

TAB_KEYS = [(f"Tab/1-{len(TABS)}", "tabs"), ("q", "quit")]
# (key, slug) pairs; Enter first wherever it acts. Drawn as a box of chips by _key_box.
LEGENDS = {
    "mixer": [("Enter", "actions"), ("↑↓", "select"), ("←→", "volume"), ("Space", "mute"),
              ("v", "meters")] + TAB_KEYS,
    "settings": [("Enter", "apply"), ("↑↓", "select"), ("←→", "change"), ("Esc", "revert"),
                 ("s", "save as preset"), ("R", "restart WirePlumber")] + TAB_KEYS,
    "top": [("↑↓ PgUp PgDn", "scroll")] + TAB_KEYS,
    "about": TAB_KEYS,
    "presets": [("Enter", "apply"), ("↑↓", "select"), ("s", "save current"), ("e", "edit label"),
                ("d", "delete")] + TAB_KEYS,
    "devices": [("Enter", "actions"), ("↑↓", "select"), ("m", "monitors"), ("h", "show hidden"), ("A", "apply files"), ("R", "restart WirePlumber")] + TAB_KEYS,
    "keys": [("Enter", "rebind"), ("↑↓", "select"), ("Bksp", "unbind")] + TAB_KEYS,
    "keys capture": [("Esc", "cancel"), ("Bksp", "unbind")],
    "devices menu": [("Enter", "pick"), ("↑↓", "choose"), ("Esc", "back")],
    "mixer menu": [("Enter", "pick"), ("↑↓", "choose"), ("Esc", "back")],
    "patch cols": [("Enter", "connect/disconnect"), ("↑↓", "select"), ("←→", "column"), ("p", "ports"),
                   ("g", "grid view"), ("m", "monitors")] + TAB_KEYS,
    "patch grid": [("Enter", "connect/disconnect"), ("↑↓", "source"), ("←→", "destination"), ("p", "ports"),
                   ("g", "column view"), ("m", "monitors")] + TAB_KEYS,
    "patch menu": [("Enter", "connect/disconnect"), ("↑↓", "choose inputs"), ("Esc", "back")],
    "patch ports": [("Enter", "link/unlink"), ("↑↓", "source port"), ("←→", "destination port"),
                    ("p Esc", "back")] + TAB_KEYS,
}
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


def legend(st):
    items = _legend(st)
    return _bound(items, st.tab, st.keys) if st.tab in pk.SCREENS and st.keys else items


def _legend(st):
    if st.tab == "devices" and st.dev_menu:
        return LEGENDS["devices menu"]
    if st.tab == "mixer" and st.mix_menu:
        return LEGENDS["mixer menu"]
    if st.tab == "settings" and st.key_capture:
        return LEGENDS["keys capture"]
    if st.tab == "settings" and KEYS_ROW <= st.cursor < RESET_KEYS_ROW:
        return LEGENDS["keys"]
    if st.tab == "settings" and st.cursor >= PRESETS_ROW:
        return LEGENDS["presets"]
    if st.tab == "patch" and st.patch_menu:
        return LEGENDS["patch menu"]
    if st.tab == "patch":
        return LEGENDS["patch ports" if st.patch_ports else f"patch {st.patch_view}"]
    return LEGENDS[st.tab]


_MARK_ROLE = {"●": "ok", "◐": "pending"}


class _Canvas:
    def __init__(self, width):
        self.width = width
        self.lines = []
        self.hits = []

    def add(self, text, style="normal"):
        """style is a role, or (base role, [(c0, c1, role), ...]) drawn in order over the base."""
        text = text[:self.width]
        marks = [(n, n + 1, _MARK_ROLE[ch]) for n, ch in enumerate(text) if ch in _MARK_ROLE]
        if marks and style != "selected":
            base, spans = style if isinstance(style, tuple) else (style, [])
            style = (base, marks + list(spans))  # explicit spans last, so a cursor bar stays one colour
        self.lines.append((text, style))
        return len(self.lines) - 1

    def hit(self, row, c0, c1, target):
        if c0 < self.width:
            self.hits.append((row, c0, min(c1, self.width), target))


def _paste_window(cv, sub, room):
    """Copy the part of sub that fits in room lines, scrolled so the cursor row (the selected one) shows."""
    cursor = next((n for n, (_, style) in enumerate(sub.lines) if style == "selected"), 0)
    rows = _window(len(sub.lines), min(len(sub.lines) - 1, cursor + 3), max(1, room))  # and a little below it
    top = len(cv.lines)
    for n in rows:
        cv.lines.append(sub.lines[n])
    cv.hits += [(top + r - rows.start, c0, c1, t) for r, c0, c1, t in sub.hits if r in rows]


def _confirm_question(confirm, devices=None):
    kind, arg = confirm[0], (confirm[1] if len(confirm) > 1 else None)
    if kind == "dev_profile":
        card = next((c for c in (devices or {"inv": pi.Inventory([], [], {})})["inv"].cards if c.id == arg), None)
        return f"Switch {card.label if card else 'the card'} to {confirm[3]}? Audio on that card drops for a moment."
    return {"restart": "Restart WirePlumber?",
            "restart_labels": "Restart WirePlumber now to show it? Apps like Discord may lose their audio.",
            "delete": f"Delete preset {arg!r}?",
            "dev_delete_bus": f"Delete bus {arg!r}? It disappears from apps now.",
            "reset_keys": "Reset every key to its default?"}[kind]


def render_screen(st, current, block, presets, width, height, devices=None, patch=None, mixer=None, media=None,
                  about=None, realtime=None):
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
    cv.add("─" * width, "dim")
    footer, chips = _key_box(legend(st), width)
    body_h = height - len(cv.lines) - 2 - len(footer)  # the now-playing bar and the status line
    if st.tab == "mixer":
        _render_mixer(cv, st, mixer, body_h)
    elif st.tab == "devices":
        _render_devices(cv, st, devices, body_h)
    elif st.tab == "patch":
        _render_patch(cv, dataclasses.replace(st), patch, body_h)
    elif st.tab == "about":
        _render_about(cv, about, st.theme)
    elif st.tab == "settings":
        sub = _Canvas(width)
        _render_settings(sub, st, current, block, presets, body_h, realtime)
        _paste_window(cv, sub, body_h)
    else:
        _render_top(cv, st, current, block, presets, body_h, realtime)
    body_end = height - 2 - len(footer)
    cv.lines = cv.lines[:body_end]
    cv.hits = [h for h in cv.hits if h[0] < body_end]
    while len(cv.lines) < body_end:
        cv.add("")
    _media_bar(cv, media)
    top = len(cv.lines)
    for text, style in footer:
        cv.add(text, style)
    for n, c0, c1, k in chips:
        cv.hit(top + n, c0, c1, ("key", k))
    if st.confirm:
        cv.add(f" {_confirm_question(st.confirm, devices)} y/n", "alert")
    else:
        style = ("alert" if st.message == st.failed else "ok") if st.message else "dim"
        cv.add(f" {st.message}" if st.message else f" {HINTS.get(st.tab, '')}", style)
    return cv.lines, cv.hits


_MEDIA_CHIPS = [("*", "play"), ("-", None), ("+", "vol"), ("<", "prev"), (">", "next")]  # numpad order
_STATUS = {"Playing": ("▶", "ok"), "Paused": ("❚❚", "pending"), "Stopped": ("■", "dim")}


def _media_bar(cv, media):
    """One line above the keys on every tab: what's playing, and its keys as clickable chips.
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


def _render_settings(cv, st, current, block, presets, body_h, realtime=None):
    st.cursor = max(0, min(PRESETS_ROW + len(presets) - 1, st.cursor))
    cv.add("  Clock", "title")
    for i, s in enumerate(SETTINGS):
        value = st.pending.get(s.key, current.get(s.key))
        mark = "*" if s.key in st.pending else " "
        prefix = "> " if i == st.cursor else "  "
        head = f"{prefix}{s.label:<20} "
        line = f"{head}◀ {display_value(s, value):^14} ▶ {mark}"
        row = cv.add(line, "selected" if i == st.cursor else ("normal", [(len(line) - 1, len(line), "pending")]))
        arrow_l, arrow_r = len(head), len(head) + 17
        cv.hit(row, 0, arrow_l, ("setting", i))
        cv.hit(row, arrow_l, arrow_l + 1, ("arrow", i, -1))
        cv.hit(row, arrow_l + 1, arrow_r, ("setting", i))
        cv.hit(row, arrow_r, arrow_r + 1, ("arrow", i, +1))
    allowed = current.get("clock.allowed-rates")
    allowed = ", ".join(map(str, allowed)) if isinstance(allowed, list) else "?"
    cv.add(f"  allowed rates: {allowed}   defaults: rate {current.get('clock.rate', '?')}"
           f" · quantum {current.get('clock.quantum', '?')}", "dim")
    try:
        warnings = validate(current, st.pending) if st.pending else []
    except ValueError as e:
        warnings = [str(e)]
    for w in warnings:
        cv.add(f"  ! {w}", "alert")
    cv.add("")
    _render_realtime(cv, realtime)
    cv.add("")
    cv.add("  Display", "title")
    _display_row(cv, st, THEME_ROW, "Theme", theme_label(st.theme), "applies at once")
    _display_row(cv, st, FPS_ROW, "Meter frame rate", f"{st.meter_fps} fps", "how often Mixer meters redraw")
    _display_row(cv, st, FALL_ROW, "Meter fall", f"{st.meter_fall} dB/s", "how fast a meter drops after a peak")
    _display_row(cv, st, HOLD_ROW, "Peak hold", f"{st.peak_hold:g} s" if st.peak_hold else "off",
                 f"a peak marker hangs, then falls {PEAK_FALL} dB/s")
    _display_row(cv, st, BAR_ROW, "Volume bar", "on" if st.volume_bar else "off", "the bar under each Mixer channel")
    cv.add("")
    _render_keys(cv, st)
    cv.add("")
    cv.add("  Presets", "title")
    _render_presets(cv, st, presets)


def _render_keys(cv, st):
    """One row per shortcut: its key, * when changed, and why it can't fire when it clashes."""
    cv.add("  Keys", "title")
    b = pk.bindings(st.keys)
    bad = pk.clashes(b)
    for n, a in enumerate(pk.ACTIONS):
        idx = KEYS_ROW + n
        k = "…" if st.key_capture == a.id else (b[a.id] or "—")
        mark = "*" if b[a.id] != a.default else " "
        note = bad.get(a.id) or ("press a key" if st.key_capture == a.id else "")
        line = f"{'> ' if st.cursor == idx else '  '}{pk.label(a):<34} {k:^5} {mark} {note}".rstrip()
        row = cv.add(line, "selected" if st.cursor == idx else "alert" if a.id in bad else "normal")
        cv.hit(row, 0, len(line), ("setting", idx))
    line = f"{'> ' if st.cursor == RESET_KEYS_ROW else '  '}Reset all keys"
    row = cv.add(line, "selected" if st.cursor == RESET_KEYS_ROW else "normal")
    cv.hit(row, 0, len(line), ("setting", RESET_KEYS_ROW))


def _render_realtime(cv, status):
    """Read-only rows (no cursor): is PipeWire's audio thread realtime, and the fix when it isn't."""
    cv.add("  Realtime", "title")
    if status is None:
        cv.add("  checking…", "dim")
        return
    if status == "fixtures":
        cv.add("  not checked (fixtures)", "dim")
        return
    for label, mark, text in prt.rows(status):
        cv.add(f"  {label:<20} {mark + ' ' if mark else ''}{text}", "normal" if mark else "dim")
    for n, line in enumerate(prt.fix(status)):
        head = "  fix: " if n == 0 else "       "
        for k, part in enumerate(textwrap.wrap(line, max(10, cv.width - len(head)), break_on_hyphens=False)):
            cv.add((head if k == 0 else " " * len(head)) + part, "ok" if status.ok else "pending")


def _display_row(cv, st, idx, label, value, note):
    head = f"{'> ' if st.cursor == idx else '  '}{label:<20} "
    row = cv.add(f"{head}◀ {value:^14} ▶   {note}", "selected" if st.cursor == idx else "normal")
    arrow_l, arrow_r = len(head), len(head) + 17
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


def theme_logo(theme):
    """The wordmark for a theme when it fits; dark and light keep ANSI Compact."""
    if theme.startswith("pipeboy"):
        return LOGO_COMPUTER
    return {"crowbar": LOGO_DOOM, "vaporware": LOGO_SLANT, "pipecraft": LOGO_PIPECRAFT}.get(theme, LOGO_ANSI)


def logo_chain(theme):
    """Wordmarks to try, widest first; the last always fits."""
    if theme == "pipecraft":
        return [LOGO_PIPECRAFT, LOGO_PIPECRAFT_SMALL, ["P I P E C R A F T"]]
    return [theme_logo(theme), LOGO_PAGGA, LOGO_PLAIN]
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
        plate = [(pad + n, pad + n + 1, "dim") for n, ch in enumerate(row) if ch == "░"]
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


def _render_top(cv, st, current, block, presets, body_h, realtime=None):
    if block is None:
        cv.add("  waiting for pw-top…", "dim")
        return
    row = cv.add(_TOP_HDR, "title")
    cv.hit(row, 0, cv.width, ("toplist",))
    visible = max(0, body_h - 1)
    start = min(st.top_scroll, max(0, len(block) - visible))
    for r in block[start:start + visible]:
        name = f"+ {r.name}" if r.follower else r.name
        line = (f"{r.state:<2}{r.id:>5}{r.quant:>7}{r.rate:>7}{r.wait:>9}{r.busy:>9}{r.wq:>6}{r.bq:>6}"
                f"{r.err:>5}  {r.format:<16} {name}")
        style = "alert" if r.err > 0 else ("normal" if r.state == "R" else "dim")
        row = cv.add(line, style)
        cv.hit(row, 0, cv.width, ("toplist",))


def _render_presets(cv, st, presets):
    if not presets:
        cv.add("  no presets yet; s saves the current clock settings as one", "dim")
    for i, p in enumerate(presets):
        sel = st.cursor == PRESETS_ROW + i
        line = f"{'> ' if sel else '  '}{p['name']:<16} {p['label']:<28} {summarize_preset(p)}"
        row = cv.add(line, "selected" if sel else "normal")
        cv.hit(row, 0, cv.width, ("preset", i))


# Always on screen in the Devices tab; the bottom help line is too narrow and gives way to messages.


def _fit(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def _device_line_text(kind, obj, devices, selected):
    prefix = "> " if selected else "  "
    if kind == "card" and obj.active == "off":
        return f"{prefix}{obj.label} · off", "dim"
    if kind == "card":
        return f"{prefix}{obj.label} · profile {obj.active or '?'}", "title"
    if kind in ("virtual", "off"):
        return f"  {kind.title()}", "title"
    if kind == "addbus":
        return f"{prefix}+ Add bus…", "dim"
    if kind == "pending":
        return f"{prefix}+ duplex {_fit(obj['label'], 30):<30} pending: A creates it  {obj['name']}", "pending"
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


def _render_devices(cv, st, devices, body_h):
    if devices is None:
        cv.add("  loading devices…", "dim")
        return
    lines, n_sel = [], 0
    selectable = dev_selectable(devices, st)
    cursor = max(0, min(len(selectable) - 1, st.dev_cursor)) if selectable else -1
    menu_at = None
    for kind, obj in _dev_lines(devices, st):
        idx = None if kind in ("virtual", "off", "chan") else n_sel
        text, style = _device_line_text("row" if kind == "chan" else kind, obj, devices, idx == cursor)
        style = "dim" if kind == "chan" else style
        menu_open = idx == cursor and _menu_target(devices, st) is not None
        lines.append((text, "anchor" if menu_open else "selected" if idx == cursor else style, idx))
        if idx is not None:
            n_sel += 1
        if idx == cursor and _menu_target(devices, st) is not None:
            title = obj.label if kind in ("row", "card") else obj["name"]
            lines += _menu_lines(devices, st, title)
            menu_at = len(lines) - 1
    room = max(1, body_h - 2)  # blank line and note sit below
    at = menu_at if menu_at is not None else next((n for n, line in enumerate(lines) if line[1] == "selected"), 0)
    start = 0 if at < room else at - room + 1
    for text, style, idx in lines[start:start + room]:
        row = cv.add(text, style)
        if isinstance(idx, tuple):
            cv.hit(row, 4, cv.width, ("dev_menu_item", idx[1]))
        elif idx is not None:
            cv.hit(row, 0, cv.width, ("device", idx))
    cv.add("")
    if devices["dirty"]:
        cv.add("  * label or clock changes not written yet; A writes them (no restart)", "pending")
    else:
        cv.add("  generated files match devices.json", "dim")


# --- Mixer tab: everyday volume, mute and defaults (notes/mixer-plan.md) --------------------------

def mixer_view(inv, config, streams):
    """What the Mixer lists: devices (not hidden, not monitors) and app streams, by direction."""
    hidden = set(config["hidden"])
    hidden_cards = {c.id for c in inv.cards if c.name in hidden}
    devs = [r for r in inv.rows() if r.kind != "monitor" and r.name not in hidden and r.card_id not in hidden_cards]
    return {"defaults": inv.defaults,
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
            return st, [("mix_cmd", pi.volume_cmd(row, "+5%" if key == "right" else "-5%"))]
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
    if kind == "app":
        return mute + [("Back", ("back",))] if mute else []
    items = [("★ System default", ("back",)) if _is_default(mx, section, row)
             else ("Set as system default", ("default",))] + mute
    if section == "playback" and row.direction in ("out", "duplex"):
        items.append(("Test sound", ("test",)))
    items.append(("Rename…", ("rename",)))
    if row.card_id is not None:  # buses have no card
        items.append(("Card profile…", ("profile",)))
    return items + [("Back", ("back",))]


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
            picks.append((n != cursor, n, (section, row.id), (row.serial, kind == "dev" and section == "playback")))
    return {k: target for _, _, k, target in sorted(picks)[:METER_CAP]}


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
    return "ok" if cell < 10 else "pending" if cell < 14 else "alert"


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
        spans = [(0, min(n, _BAR_FULL), "ok")] + ([(_BAR_FULL, n, "alert")] if n > _BAR_FULL else []) + [(n, _BAR, "dim")]
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
    label_w = max(20, min(44, cv.width - vol_w - 6 - (_METER + 2 if metered else 0)))
    out, n_sel = [], 0
    for kind, section, row in _mixer_lines(mx):
        if kind == "head":
            out.append((f"  {section}", "title", None))
            continue
        if kind == "sub":
            out.append((f"    {section}", "dim", None))
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
        bar, spans = _volume_bar(row, st.volume_bar)
        bar = bar.ljust(vol_w)
        c0 = len(head)
        spans = [(c0 + a, c0 + b, r) for a, b, r in spans]
        if (section, row.id) in metered:
            level = levels.get((section, row.id), 0.0)
            cells, cell_spans = _meter_cells(*(level if isinstance(level, tuple) else (level,)))
            m0 = c0 + len(bar) + 2
            bar += "  " + cells
            spans += [(m0 + a, m0 + b, r) for a, b, r in cell_spans]
        if sel:  # the cursor bar covers the name; the volume keeps its colours
            spans = [(0, c0 - 1, "anchor" if target else "selected")] + spans
        out.append((head + bar, ("normal", spans), n_sel))
        if sel and target:
            out += _menu_box(row.label, [label for label, _ in mix_menu_items(mx, st)], st.mix_menu_cursor)
        n_sel += 1
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

_STATE_MARK = {"full": "●", "partial": "◐", "none": "·"}


def patch_sources(g, st):
    """Monitors only with m, except one something records from (OBS's Desktop Audio): a live link never hides."""
    return [s for s in g.sources if st.patch_monitors or not s.monitor or g.targets(s)]


def _with_monitor_head(srcs, indices):
    """The source indices to draw, with "head" before the first monitor (monitors sort last)."""
    out = []
    for i in indices:
        if srcs[i].monitor and (not out or out[-1] == "head" or not srcs[out[-1]].monitor):
            out.append("head")
        out.append(i)
    return out


def _patch_pair(g, st):
    """(sources, source, destination) under the cursors, clamping them; None when nothing to pick."""
    srcs = patch_sources(g, st)
    if not srcs or not g.dests:
        return srcs, None, None
    st.patch_src = max(0, min(len(srcs) - 1, st.patch_src))
    st.patch_dst = max(0, min(len(g.dests) - 1, st.patch_dst))
    return srcs, srcs[st.patch_src], g.dests[st.patch_dst]


def _toggle(g, st, s, d):
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


def _window(n, cursor, room):
    start = 0 if cursor < room else cursor - room + 1
    return range(start, min(n, start + room))


def _pair_status(g, s, d):
    state = g.state(s, d)
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
        cv.add(_pair_status(g, s, d), "alert" if g.state(s, d) != "none" else "dim")


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
    out = [("┌" + f"─ {_fit(s.label, w - 3)} ".ljust(w, "─") + "┐", None, None)]
    for k, text in enumerate(items):
        sel = k == st.patch_menu_cursor
        out.append(("│" + f"{'>' if sel else ' '} {_fit(text, w - 2)}".ljust(w) + "│", "selected" if sel else None,
                    ("patch_slot", k)))
    out.append(("└" + "─" * w + "┘", None, None))
    return out


def _render_patch_cols(cv, st, g, srcs, s, room):
    half = max(20, (cv.width - 3) // 2)
    cv.add(f"  {'Sources':<{half - 2}}│ Destinations", "title")
    on_dst = st.patch_side == "dst"
    menu = _slot_menu(g, st, s, g.dests[st.patch_dst], cv.width - half - 2) if on_dst else []
    has_mon = any(s_.monitor for s_ in srcs)
    left = _with_monitor_head(srcs, list(_window(len(srcs), st.patch_src, room - 1 - has_mon)))
    right = []  # (text, highlighted, hit target); the slot menu sits under its destination
    for j in _window(len(g.dests), st.patch_dst, max(1, room - 1 - len(menu))):
        mark = "> " if j == st.patch_dst and on_dst else "  "
        state = g.state(s, g.dests[j])
        rt = f"{mark}{_STATE_MARK[state].replace('·', ' ')} {_fit(g.dests[j].label, half - 4)}"
        lit = ("anchor" if menu else "selected") if j == st.patch_dst and on_dst else None
        right.append((rt, lit, ("patch_dst", j), {"full": "dst", "partial": "dst_partial"}.get(state)))
        if j == st.patch_dst:
            right += menu
    for n in range(max(len(left), len(right))):
        lt, spans = "", []
        if n < len(left) and left[n] == "head":
            lt, spans = "  Monitors", [(0, half, "title")]
        elif n < len(left):
            i = left[n]
            count = len(g.targets(srcs[i]))
            mark = "> " if i == st.patch_src and not on_dst else "* " if i == st.patch_src else "  "
            lt = f"{mark}{_fit(srcs[i].label, half - 7):<{half - 7}} {('●' + str(count)) if count else '':>3}"
            if count:  # the source's dot: blue in Portal; dots go first so a cursor bar still covers them
                spans.append((lt.index("●"), lt.index("●") + 1, "src"))
            if i == st.patch_src:  # each bar covers only its own column; the source's stays as the anchor
                spans.append((0, half, "anchor" if on_dst else "selected"))
        rt, lit, target, dot = (right[n] + (None,))[:4] if n < len(right) else ("", None, None, None)
        if dot:
            spans.append((half + 2 + 2, half + 2 + 3, dot))
        if lit:
            spans.append((half + 2, cv.width if target[0] == "patch_dst" else half + 2 + len(rt), lit))
        row = cv.add(f"{lt:<{half}}│ {rt}".ljust(cv.width), ("normal", spans))
        if n < len(left) and left[n] != "head":
            cv.hit(row, 0, half, ("patch_src", left[n]))
        if target:
            cv.hit(row, half + 1, cv.width, target)


def _render_patch_grid(cv, st, g, srcs, s, d, room):
    label_w = 30
    cols_fit = max(1, (cv.width - label_w - 3) // 3)
    cols = list(_window(len(g.dests), st.patch_dst, cols_fit))
    key_rows = -(-len(cols) // max(1, (cv.width - 2) // 35))
    menu = _slot_menu(g, st, s, d, cv.width - 4)
    has_mon = any(s_.monitor for s_ in srcs)
    rows = list(_window(len(srcs), st.patch_src, max(1, room - 2 - key_rows - len(menu) - has_mon)))
    cv.add(" " * (label_w + 2) + "".join(f"{j + 1:>3}" for j in cols), "title")
    for i in _with_monitor_head(srcs, rows):
        if i == "head":
            cv.add("  Monitors", "title")
            continue
        cells = ""
        for j in cols:
            mark = _STATE_MARK[g.state(srcs[i], g.dests[j])]
            cells += f"[{mark}]" if (i, j) == (st.patch_src, st.patch_dst) else f" {mark} "
        sel = i == st.patch_src
        spans = []
        if sel:
            c0 = 2 + label_w + 3 * cols.index(st.patch_dst) if st.patch_dst in cols else None
            spans = [(0, 2 + label_w, "anchor")] + ([(c0, c0 + 3, "selected")] if c0 is not None else [])
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
            spans = [(0, 2 + row_w, "anchor")] + ([(c0, c0 + cell_w, "selected")] if c0 is not None else [])
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
    return [("Unhide" if obj.name in hidden else "Hide", ("hide", obj.name)), ("Delete bus", ("delete", obj.name)),
            ("Back", ("back",))]


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
        st.confirm = ("dev_delete_bus", op[1])
    elif op[0] == "create":
        return st, [("dev_bus_create", op[1])]
    elif op[0] == "profile" and op[2] != obj.active:
        st.confirm = ("dev_profile", obj.id, op[1], op[2])
    return st, []


def _menu_lines(devices, st, title):
    return _menu_box(title, [label for label, _ in dev_menu_items(devices, st)], st.dev_menu_cursor)


def _menu_box(title, labels, cursor):
    """A box under the selected row (Devices and Mixer). Every line shares one interior width, so the
    edges line up. Item lines carry ("menu", k) for their click target."""
    width = max(26, len(title) + 4, max((len(label) for label in labels), default=0) + 5)
    lines = [("    ┌" + f"─ {title} ".ljust(width, "─") + "┐", "title", None)]
    for k, label in enumerate(labels):
        sel = k == cursor
        lines.append(("    │" + f" {'> ' if sel else '  '}{label}".ljust(width) + "│",
                      "selected" if sel else "normal", ("menu", k)))
    lines.append(("    └" + "─" * width + "┘", "title", None))
    return lines
