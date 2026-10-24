"""Pure logic for pwctl: PipeWire settings metadata, pw-top parsing, presets, and the TUI
state machine. No curses and no subprocess calls here; callers pass a runner.

Behaviour follows PipeControl (github.com/portaloffreedom/pipecontrol v0.3.0):
settings keys from src/pw/qpipewiresettings.cpp, choice lists from resources/Settings.qml.
"""
import dataclasses
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pwinventory as pi
import pwpatch as pp

READ_CMD = ["pw-metadata", "-n", "settings", "0"]
RESTART_CMD = ["systemctl", "--user", "restart", "wireplumber"]


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
    if os.environ.get("PWCTL_PRESETS"):
        return Path(os.environ["PWCTL_PRESETS"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "pwctl" / "presets.json"


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

TABS = ["settings", "top", "presets", "devices", "patch"]
TAB_TITLES = {"settings": "1 Settings", "top": "2 Top", "presets": "3 Presets", "devices": "4 Devices", "patch": "5 Patch"}


@dataclass
class UIState:
    tab: str = "settings"
    cursor: int = 0
    pending: dict = field(default_factory=dict)
    preset_cursor: int = 0
    top_scroll: int = 0
    dev_cursor: int = 0
    dev_show_hidden: bool = False
    dev_monitors: bool = False
    dev_menu: tuple = None       # ("row", node name) | ("card", id) | ("pending", bus name)
    dev_menu_cursor: int = 0
    patch_view: str = "cols"     # cols | grid
    patch_side: str = "src"      # src | dst (column view)
    patch_src: int = 0
    patch_dst: int = 0
    patch_monitors: bool = False
    patch_ports: bool = False
    patch_port_row: int = 0      # source port in the port matrix
    patch_port_col: int = 0      # destination port
    confirm: tuple = None
    message: str = ""


def handle_key(st, key, current, presets, devices=None, patch=None):
    """Return (new_state, actions). Nothing touches PipeWire until an action is executed.

    devices is the Devices tab's view: {"model", "status", "buses", "dirty"}, or None until loaded.
    """
    st = dataclasses.replace(st, pending=dict(st.pending))
    if st.confirm:
        if key == "y":
            kind = st.confirm
            st.confirm = None
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
    if key in ("1", "2", "3", "4", "5"):
        st.tab = TABS[int(key) - 1]
        return st, []
    if st.tab == "devices":
        return _key_devices(st, key, devices)
    if st.tab == "patch":
        return _key_patch(st, key, patch)
    return {"settings": _key_settings, "top": _key_top, "presets": _key_presets}[st.tab](st, key, current, presets)


def _key_settings(st, key, current, presets):
    if key in ("up", "down"):
        st.cursor = max(0, min(len(SETTINGS) - 1, st.cursor + (1 if key == "down" else -1)))
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
            st.message = str(e)
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


def _key_presets(st, key, current, presets):
    if key == "s":
        return st, [("prompt_save",)]
    if not presets:
        return st, []
    st.preset_cursor = min(st.preset_cursor, len(presets) - 1)
    name = presets[st.preset_cursor]["name"]
    if key in ("up", "down"):
        st.preset_cursor = max(0, min(len(presets) - 1, st.preset_cursor + (1 if key == "down" else -1)))
    elif key == "enter":
        return st, [("apply_preset", name)]
    elif key == "e":
        return st, [("prompt_label", name)]
    elif key == "d":
        st.confirm = ("delete", name)
    return st, []


def devices_view(inv, config, dirty=False, owned=(), clash=(), pending=()):
    """Everything the Devices tab draws: the live inventory, devices.json, whether the generated
    files are behind it, and bus status (pwctl-owned, clashing with a bus made elsewhere, pending)."""
    return {"inv": inv, "config": config, "dirty": dirty, "owned": set(owned), "clash": set(clash),
            "pending": list(pending)}


def _dev_lines(devices, st):
    """(kind, obj) per list line: card, virtual (the heading), row or pending (a bus not yet created)."""
    inv, hidden = devices["inv"], set(devices["config"]["hidden"])

    def rows(rs):
        rs = list(rs) + (pi.monitor_rows(rs) if st.dev_monitors else [])
        return [("row", r) for r in rs if st.dev_show_hidden or r.name not in hidden]

    lines = []
    for card in inv.cards:
        if card.name in hidden and not st.dev_show_hidden:
            continue
        lines.append(("card", card))
        lines += rows(card.rows)
    lines.append(("virtual", None))
    lines += rows(inv.virtual)
    lines += [("pending", b) for b in devices["pending"]]
    lines.append(("addbus", None))
    return lines


def dev_selectable(devices, st):
    return [line for line in _dev_lines(devices, st) if line[0] != "virtual"]


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
    if key == "H":
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
    elif key in ("d", "left", "right", " "):
        if kind != "row":
            st.message = "select a device row first"
            return st, []
        try:
            cmd = {"d": pi.default_cmd, " ": pi.mute_cmd,
                   "left": lambda r: pi.volume_cmd(r, "-5%"), "right": lambda r: pi.volume_cmd(r, "+5%")}[key](obj)
        except ValueError as e:
            st.message = str(e)
            return st, []
        return st, [("dev_cmd", cmd)]
    elif key == "enter":
        if kind == "addbus":
            return st, [("dev_prompt_bus",)]
        _open_menu(st, kind, obj)
    elif key == "p":
        card_id = obj.id if kind == "card" else obj.card_id if kind == "row" else None
        card = next((c for c in devices["inv"].cards if c.id == card_id), None)
        if card is None:
            st.message = "virtual devices have no card profile"
            return st, []
        _open_menu(st, "card", card)
    elif key == "h":
        if kind in ("pending", "addbus"):
            st.message = "a pending bus isn't running yet; x deletes it"
            return st, []
        return st, [("dev_hide", obj.name)]
    elif key == "x":
        if kind == "pending" or (kind == "row" and obj.kind == "virtual"):
            st.confirm = ("dev_delete_bus", obj["name"] if kind == "pending" else obj.name)
        elif kind == "row" and obj.kind == "hardware":
            st.message = "hardware can't be deleted; p switches its card profile"
        else:
            st.message = "x deletes a virtual bus"
    elif key == "t":
        if kind == "row" and obj.kind != "monitor" and obj.direction in ("out", "duplex"):
            return st, [("dev_test", obj.name)]
        st.message = "t plays a test noise on an output"
    return st, []


_MOUSE_BUTTONS = {"apply": "enter", "revert": "esc", "restart": "R", "preset_apply": "enter",
                  "preset_save": "s", "preset_edit": "e", "preset_delete": "d",
                  "dev_default": "d", "dev_mute": " ", "dev_label": "enter", "dev_profile": "p",
                  "dev_add_bus": "a", "dev_apply": "A"}


def handle_mouse(st, hits, y, x, event, current, presets, devices=None, patch=None):
    """Map a mouse event onto the same keys the keyboard uses."""
    if st.confirm:
        return dataclasses.replace(st, confirm=None, message="cancelled (y confirms)"), []
    target = next((t for row, c0, c1, t in hits if row == y and c0 <= x < c1), None)
    if target is None:
        return st, []
    kind = target[0]
    keys = []
    if kind == "tab":
        return dataclasses.replace(st, tab=target[1]), []
    if kind == "setting":
        st = dataclasses.replace(st, cursor=target[1])
        keys = {"double": ["enter"], "wheel_up": ["right"], "wheel_down": ["left"]}.get(event, [])
    elif kind == "arrow":
        st = dataclasses.replace(st, cursor=target[1])
        keys = ["right" if target[2] > 0 else "left"] if event in ("click", "double") else []
    elif kind == "button":
        keys = [_MOUSE_BUTTONS[target[1]]] if event in ("click", "double") else []
    elif kind == "preset":
        st = dataclasses.replace(st, preset_cursor=target[1])
        keys = ["enter"] if event == "double" else []
    elif kind == "toplist":
        keys = {"wheel_down": ["down"], "wheel_up": ["up"]}.get(event, [])
    elif kind == "dev_menu_item":
        st = dataclasses.replace(st, dev_menu_cursor=target[1])
        keys = ["enter"] if event in ("click", "double") else []
    elif kind == "device":
        st = dataclasses.replace(st, dev_cursor=target[1], dev_menu=None)
        keys = {"double": ["enter"], "wheel_down": ["down"], "wheel_up": ["up"]}.get(event, [])
    elif kind in ("patch_src", "patch_dst"):
        side = "src" if kind == "patch_src" else "dst"
        st = dataclasses.replace(st, patch_side=side, **{f"patch_{side}": target[1]})
        keys = {"double": ["enter"] if side == "dst" else [], "wheel_down": ["down"], "wheel_up": ["up"]}.get(event, [])
    elif kind == "patch_cell":
        st = dataclasses.replace(st, patch_src=target[1], patch_dst=target[2])
        keys = ["enter"] if event == "double" else []
    elif kind == "patch_pcell":
        st = dataclasses.replace(st, patch_port_row=target[1], patch_port_col=target[2])
        keys = ["enter"] if event == "double" else []
    actions = []
    for k in keys:
        st, a = handle_key(st, k, current, presets, devices, patch)
        actions += a
    return st, actions


# --- rendering (view model) --------------------------------------------------

HELP = {
    "settings": "↑↓ select  ←→ change  Enter apply  Esc revert  R restart WP  Tab switch  q quit",
    "top": "↑↓ PgUp/PgDn scroll  Tab switch  q quit",
    "presets": "↑↓ select  Enter apply  s save current  e edit label  d delete  Tab switch  q quit",
    "devices": "buses and d ←→ Space p act now · labels show after R or next login",
    "patch": "links made here are live only; saved patch sets come with scenes",
}


class _Canvas:
    def __init__(self, width):
        self.width = width
        self.lines = []
        self.hits = []

    def add(self, text, style="normal"):
        self.lines.append((text[:self.width], style))
        return len(self.lines) - 1

    def hit(self, row, c0, c1, target):
        if c0 < self.width:
            self.hits.append((row, c0, min(c1, self.width), target))


def _buttons(cv, labels):
    text, spans = "  ", []
    for target, label in labels:
        spans.append((len(text), len(text) + len(label) + 2, target))
        text += f"[{label}]  "
    row = cv.add(text.rstrip(), "button")
    for c0, c1, target in spans:
        cv.hit(row, c0, c1, ("button", target))


def _confirm_question(confirm, devices=None):
    kind, arg = confirm[0], (confirm[1] if len(confirm) > 1 else None)
    if kind == "dev_profile":
        card = next((c for c in (devices or {"inv": pi.Inventory([], [], {})})["inv"].cards if c.id == arg), None)
        return f"Switch {card.label if card else 'the card'} to {confirm[3]}? Audio on that card drops for a moment."
    return {"restart": "Restart WirePlumber?",
            "restart_labels": "Restart WirePlumber now to show it? Apps like Discord may lose their audio.",
            "delete": f"Delete preset {arg!r}?",
            "dev_delete_bus": f"Delete bus {arg!r}? It disappears from apps now."}[kind]


def render_screen(st, current, block, presets, width, height, devices=None, patch=None):
    cv = _Canvas(width)
    text, spans = " ", []
    for tab in TABS:
        label = f"[{TAB_TITLES[tab]}]" if tab == st.tab else f" {TAB_TITLES[tab]} "
        spans.append((len(text), len(text) + len(label), tab))
        text += label + " "
    row = cv.add(text, "title")
    for c0, c1, tab in spans:
        cv.hit(row, c0, c1, ("tab", tab))
    cv.add("─" * width, "dim")
    body_h = height - len(cv.lines) - 1
    if st.tab == "devices":
        _render_devices(cv, st, devices, body_h)
    elif st.tab == "patch":
        _render_patch(cv, dataclasses.replace(st), patch, body_h)
    else:
        {"settings": _render_settings, "top": _render_top, "presets": _render_presets}[st.tab](
            cv, st, current, block, presets, body_h)
    cv.lines = cv.lines[:height - 1]
    cv.hits = [h for h in cv.hits if h[0] < height - 1]
    while len(cv.lines) < height - 1:
        cv.add("")
    if st.confirm:
        cv.add(f" {_confirm_question(st.confirm, devices)} y/n", "alert")
    else:
        cv.add(f" {st.message}" if st.message else f" {HELP[st.tab]}", "alert" if st.message else "dim")
    return cv.lines, cv.hits


def _render_settings(cv, st, current, block, presets, body_h):
    for i, s in enumerate(SETTINGS):
        value = st.pending.get(s.key, current.get(s.key))
        mark = "*" if s.key in st.pending else " "
        prefix = "> " if i == st.cursor else "  "
        head = f"{prefix}{s.label:<20} "
        line = f"{head}◀ {display_value(s, value):^14} ▶ {mark}"
        row = cv.add(line, "selected" if i == st.cursor else "normal")
        arrow_l, arrow_r = len(head), len(head) + 17
        cv.hit(row, 0, arrow_l, ("setting", i))
        cv.hit(row, arrow_l, arrow_l + 1, ("arrow", i, -1))
        cv.hit(row, arrow_l + 1, arrow_r, ("setting", i))
        cv.hit(row, arrow_r, arrow_r + 1, ("arrow", i, +1))
    cv.add("")
    _buttons(cv, [("apply", "Apply"), ("revert", "Revert"), ("restart", "Restart WirePlumber")])
    cv.add("")
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


_TOP_HDR = f"{'S':<2}{'ID':>5}{'QUANT':>7}{'RATE':>7}{'WAIT':>9}{'BUSY':>9}{'W/Q':>6}{'B/Q':>6}{'ERR':>5}  {'FORMAT':<16} NAME"


def _render_top(cv, st, current, block, presets, body_h):
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


def _render_presets(cv, st, current, block, presets, body_h):
    if not presets:
        cv.add("  no presets yet; press s (or click Save current) to save the current settings", "dim")
    for i, p in enumerate(presets):
        sel = i == min(st.preset_cursor, len(presets) - 1)
        line = f"{'> ' if sel else '  '}{p['name']:<16} {p['label']:<28} {summarize_preset(p)}"
        row = cv.add(line, "selected" if sel else "normal")
        cv.hit(row, 0, cv.width, ("preset", i))
    cv.add("")
    _buttons(cv, [("preset_apply", "Apply"), ("preset_save", "Save current"),
                  ("preset_edit", "Edit label"), ("preset_delete", "Delete")])


# Always on screen in the Devices tab; the bottom help line is too narrow and gives way to messages.
DEVICE_KEYS = [
    "  Keys: ↑↓ select  Enter actions  ←→ volume  Space mute  d default",
    "        m monitors  H show hidden  R restart WirePlumber  Tab/1-5 tabs  q quit",
]
MENU_KEYS = ["  Keys: ↑↓ choose  Enter pick  Esc back", ""]


def _fit(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def _device_line_text(kind, obj, devices, selected):
    prefix = "> " if selected else "  "
    if kind == "card":
        return f"{prefix}{obj.label} · profile {obj.active or '?'}", "title"
    if kind == "virtual":
        return "  Virtual", "title"
    if kind == "addbus":
        return f"{prefix}+ Add bus…", "dim"
    if kind == "pending":
        return f"{prefix}+ duplex {_fit(obj['label'], 30):<30} pending: A creates it  {obj['name']}", "dim"
    c = devices["config"]
    vol = "muted" if obj.mute else "" if obj.volume is None else f"{round(obj.volume * 100)}%"
    pending = c["labels"].get(obj.name)
    hidden = obj.name in c["hidden"]
    tags = [t for t, on in (("pwctl", obj.name in devices["owned"]), ("made outside pwctl", obj.name in devices["clash"]),
                            ("hidden", hidden), (f"label → {pending}", bool(pending) and pending != obj.label)) if on]
    text = (f"{prefix}{'★' if obj.default else ' '} {obj.direction:<6} {_fit(obj.label, 30):<30} "
            f"{obj.state or '?':<9} {vol:<6} {obj.id:>5}" + (f"  [{', '.join(tags)}]" if tags else "")
            + (f"  {pi.connections(obj)}" if obj.inputs or obj.outputs else ""))  # tags first: they're rare, and matter
    return text, "dim" if hidden else "normal"


def _render_devices(cv, st, devices, body_h):
    if devices is None:
        cv.add("  loading devices…", "dim")
        return
    lines, n_sel = [], 0
    selectable = dev_selectable(devices, st)
    cursor = max(0, min(len(selectable) - 1, st.dev_cursor)) if selectable else -1
    menu_at = None
    for kind, obj in _dev_lines(devices, st):
        idx = None if kind == "virtual" else n_sel
        text, style = _device_line_text(kind, obj, devices, idx == cursor)
        lines.append((text, "selected" if idx == cursor else style, idx))
        if idx is not None:
            n_sel += 1
        if idx == cursor and _menu_target(devices, st) is not None:
            title = obj.label if kind in ("row", "card") else obj["name"]
            lines += _menu_lines(devices, st, title)
            menu_at = len(lines) - 1
    room = max(1, body_h - 3 - len(DEVICE_KEYS))  # blank line, buttons, note and key legend sit below
    at = menu_at if menu_at is not None else next((n for n, line in enumerate(lines) if line[1] == "selected"), 0)
    start = 0 if at < room else at - room + 1
    for text, style, idx in lines[start:start + room]:
        row = cv.add(text, style)
        if isinstance(idx, tuple):
            cv.hit(row, 4, cv.width, ("dev_menu_item", idx[1]))
        elif idx is not None:
            cv.hit(row, 0, cv.width, ("device", idx))
    cv.add("")
    _buttons(cv, [("dev_default", "Default"), ("dev_mute", "Mute"), ("dev_label", "Actions"),
                  ("dev_profile", "Profile"), ("dev_add_bus", "Add bus"), ("dev_apply", "Apply")])
    if devices["dirty"]:
        cv.add("  * label or clock changes not written yet; A writes them (no restart)", "alert")
    else:
        cv.add("  generated files match devices.json", "dim")
    for line in (MENU_KEYS if st.dev_menu else DEVICE_KEYS):
        cv.add(line, "dim")


# --- Patch tab -------------------------------------------------------------------------

PATCH_KEYS = {
    "cols": ["  Keys: ↑↓ select  ←→ column  Enter connect/disconnect  p ports",
             "        g grid view  m monitors  Tab/1-5 tabs  q quit"],
    "grid": ["  Keys: ↑↓ source  ←→ destination  Enter connect/disconnect  p ports",
             "        g column view  m monitors  Tab/1-5 tabs  q quit"],
    "ports": ["  Keys: ↑↓ source port  ←→ destination port  Enter link/unlink  p or Esc back",
              "        Tab/1-5 tabs  q quit"],
}
_STATE_MARK = {"full": "●", "partial": "◐", "none": "·"}


def patch_sources(g, st):
    return [s for s in g.sources if st.patch_monitors or not s.monitor]


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
        st.message = f"nothing to link between {s.label} and {d.label}"
        return st, []
    return st, [("patch_cmds", cmds)]


def _key_patch(st, key, g):
    if g is None:
        return st, []
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
            return _toggle(g, st, s, d)
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
            return _toggle(g, st, s, d)
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
    room = max(1, body_h - 3 - 2)  # blank, status, blank-free legend (2 lines)
    if st.patch_ports:
        _render_port_matrix(cv, st, g, s, d, room)
        legend = PATCH_KEYS["ports"]
    elif st.patch_view == "grid":
        _render_patch_grid(cv, st, g, srcs, room)
        legend = PATCH_KEYS["grid"]
    else:
        _render_patch_cols(cv, st, g, srcs, s, room)
        legend = PATCH_KEYS["cols"]
    cv.add("")
    if st.patch_view == "cols" and st.patch_side == "src" and not st.patch_ports:
        targets = g.targets(s)
        cv.add(f"  {s.label} → {', '.join(x.label for x in targets)}" if targets
               else f"  {s.label}: not connected", "alert" if targets else "dim")
    else:
        cv.add(_pair_status(g, s, d), "alert" if g.state(s, d) != "none" else "dim")
    for line in legend:
        cv.add(line, "dim")


def _render_patch_cols(cv, st, g, srcs, s, room):
    half = max(20, (cv.width - 3) // 2)
    cv.add(f"  {'Sources':<{half - 2}}│ Destinations", "title")
    left = list(_window(len(srcs), st.patch_src, room - 1))
    right = list(_window(len(g.dests), st.patch_dst, room - 1))
    for n in range(max(len(left), len(right))):
        lt, rt = "", ""
        if n < len(left):
            i = left[n]
            count = len(g.targets(srcs[i]))
            mark = "> " if i == st.patch_src and st.patch_side == "src" else "* " if i == st.patch_src else "  "
            lt = f"{mark}{_fit(srcs[i].label, half - 7):<{half - 7}} {('●' + str(count)) if count else '':>3}"
        if n < len(right):
            j = right[n]
            mark = "> " if j == st.patch_dst and st.patch_side == "dst" else "  "
            rt = f"{mark}{_STATE_MARK[g.state(s, g.dests[j])].replace('·', ' ')} {_fit(g.dests[j].label, half - 4)}"
        selected = (n < len(left) and left[n] == st.patch_src and st.patch_side == "src")
        row = cv.add(f"{lt:<{half}}│ {rt}", "selected" if selected else "normal")
        if n < len(left):
            cv.hit(row, 0, half, ("patch_src", left[n]))
        if n < len(right):
            cv.hit(row, half + 1, cv.width, ("patch_dst", right[n]))


def _render_patch_grid(cv, st, g, srcs, room):
    label_w = 30
    cols_fit = max(1, (cv.width - label_w - 3) // 3)
    cols = list(_window(len(g.dests), st.patch_dst, cols_fit))
    key_rows = (len(cols) + 2) // 3
    rows = list(_window(len(srcs), st.patch_src, max(1, room - 2 - key_rows)))
    cv.add(" " * (label_w + 2) + "".join(f"{j + 1:>3}" for j in cols), "title")
    for i in rows:
        cells = ""
        for j in cols:
            mark = _STATE_MARK[g.state(srcs[i], g.dests[j])]
            cells += f"[{mark}]" if (i, j) == (st.patch_src, st.patch_dst) else f" {mark} "
        sel = i == st.patch_src
        row = cv.add(f"{'> ' if sel else '  '}{_fit(srcs[i].label, label_w):<{label_w}}{cells}",
                     "selected" if sel else "normal")
        for k, j in enumerate(cols):
            c0 = 2 + label_w + 3 * k
            cv.hit(row, c0, c0 + 3, ("patch_cell", i, j))
    cv.add("")
    for start in range(0, len(cols), 3):
        cv.add("  " + "".join(f"{j + 1:>3} {_fit(g.dests[j].label, 30):<31}" for j in cols[start:start + 3]), "dim")


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
        row = cv.add(f"{'> ' if sel else '  '}{s.ports[i].channel:<{row_w}}{cells}", "selected" if sel else "normal")
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
        return items + [("Unhide card" if obj.name in hidden else "Hide card", ("hide", obj.name)), ("Back", ("back",))]
    if kind == "pending":
        return [("Create now", ("create", obj["name"])), ("Rename…", ("rename", obj["name"], obj["label"])),
                ("Delete bus", ("delete", obj["name"])), ("Back", ("back",))]
    hide = ("Unhide" if obj.name in hidden else "Hide", ("hide", obj.name))
    if obj.kind == "monitor":
        return [hide, ("Back", ("back",))]
    items = [("Rename…", ("rename", obj.name, obj.label)), ("Make default", ("default",)),
             ("Unmute" if obj.mute else "Mute", ("mute",))]
    if obj.direction in ("out", "duplex"):
        items.append(("Test sound", ("test", obj.name)))
    items.append(hide)
    if obj.kind == "virtual":
        items.append(("Delete bus", ("delete", obj.name)))
    return items + [("Back", ("back",))]


def _open_menu(st, kind, obj):
    if kind == "card":
        st.dev_menu = ("card", obj.id)
        names = [p.name for p in obj.profiles]
        st.dev_menu_cursor = names.index(obj.active) if obj.active in names else 0
    else:
        st.dev_menu = (kind, obj.name if kind == "row" else obj["name"])
        st.dev_menu_cursor = 0


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
    if op[0] in ("default", "mute"):
        try:
            return st, [("dev_cmd", (pi.default_cmd if op[0] == "default" else pi.mute_cmd)(obj))]
        except ValueError as e:
            st.message = str(e)
            return st, []
    if op[0] == "test":
        return st, [("dev_test", op[1])]
    if op[0] == "hide":
        return st, [("dev_hide", op[1])]
    if op[0] == "delete":
        st.confirm = ("dev_delete_bus", op[1])
    elif op[0] == "create":
        return st, [("dev_bus_create", op[1])]
    elif op[0] == "profile" and op[2] != obj.active:
        st.confirm = ("dev_profile", obj.id, op[1], op[2])
    return st, []


def _menu_lines(devices, st, title):
    """A box under the selected row. Every line shares one interior width, so the edges line up."""
    items = dev_menu_items(devices, st)
    width = max(26, len(title) + 4, max((len(label) for label, _ in items), default=0) + 5)
    lines = [("    ┌" + f"─ {title} ".ljust(width, "─") + "┐", "title", None)]
    for k, (label, _) in enumerate(items):
        sel = k == st.dev_menu_cursor
        lines.append(("    │" + f" {'> ' if sel else '  '}{label}".ljust(width) + "│",
                      "selected" if sel else "normal", ("menu", k)))
    lines.append(("    └" + "─" * width + "┘", "title", None))
    return lines
