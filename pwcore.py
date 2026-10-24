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

TABS = ["settings", "top", "presets", "devices"]
TAB_TITLES = {"settings": "1 Settings", "top": "2 Top", "presets": "3 Presets", "devices": "4 Devices"}


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
    confirm: tuple = None
    message: str = ""


def handle_key(st, key, current, presets, devices=None):
    """Return (new_state, actions). Nothing touches PipeWire until an action is executed.

    devices is the Devices tab's view: {"model", "status", "buses", "dirty"}, or None until loaded.
    """
    st = dataclasses.replace(st, pending=dict(st.pending))
    if st.confirm:
        if key == "y":
            kind = st.confirm
            st.confirm = None
            # confirm tuples name their action, except the two v1 ones
            return st, [{"restart": ("restart_wireplumber",), "delete": ("delete_preset", *kind[1:])}.get(kind[0], kind)]
        if key in ("n", "esc"):
            st.confirm, st.message = None, "cancelled"
        return st, []
    if key == "q":
        return st, [("quit",)]
    if key == "tab":
        st.tab = TABS[(TABS.index(st.tab) + 1) % len(TABS)]
        return st, []
    if key in ("1", "2", "3", "4"):
        st.tab = TABS[int(key) - 1]
        return st, []
    if st.tab == "devices":
        return _key_devices(st, key, devices)
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
    return lines


def dev_selectable(devices, st):
    return [line for line in _dev_lines(devices, st) if line[0] != "virtual"]


def _key_devices(st, key, devices):
    if devices is None:
        return st, []
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
        if kind == "pending":
            return st, [("dev_prompt_label", obj["name"], obj["label"])]
        if kind == "row" and obj.kind != "monitor":
            return st, [("dev_prompt_label", obj.name, obj.label)]
        st.message = "Enter labels a device row; p switches this card's profile"
    elif key == "p":
        card_id = obj.id if kind == "card" else obj.card_id if kind == "row" else None
        if card_id is None:
            st.message = "virtual devices have no card profile"
            return st, []
        return st, [("dev_prompt_profile", card_id)]
    elif key == "h":
        if kind == "pending":
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


def handle_mouse(st, hits, y, x, event, current, presets, devices=None):
    """Map a mouse event onto the same keys the keyboard uses."""
    if st.confirm:
        return st, []
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
    elif kind == "device":
        st = dataclasses.replace(st, dev_cursor=target[1])
        keys = {"double": ["enter"], "wheel_down": ["down"], "wheel_up": ["up"]}.get(event, [])
    actions = []
    for k in keys:
        st, a = handle_key(st, k, current, presets, devices)
        actions += a
    return st, actions


# --- rendering (view model) --------------------------------------------------

HELP = {
    "settings": "↑↓ select  ←→ change  Enter apply  Esc revert  R restart WP  Tab switch  q quit",
    "top": "↑↓ PgUp/PgDn scroll  Tab switch  q quit",
    "presets": "↑↓ select  Enter apply  s save current  e edit label  d delete  Tab switch  q quit",
    "devices": "buses and d ←→ Space p act now · labels show after R or next login",
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


def _confirm_question(confirm):
    kind, arg = confirm[0], (confirm[1] if len(confirm) > 1 else None)
    return {"restart": "Restart WirePlumber?",
            "delete": f"Delete preset {arg!r}?",
            "dev_delete_bus": f"Delete bus {arg!r}? It disappears from apps now."}[kind]


def render_screen(st, current, block, presets, width, height, devices=None):
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
    else:
        {"settings": _render_settings, "top": _render_top, "presets": _render_presets}[st.tab](
            cv, st, current, block, presets, body_h)
    cv.lines = cv.lines[:height - 1]
    cv.hits = [h for h in cv.hits if h[0] < height - 1]
    while len(cv.lines) < height - 1:
        cv.add("")
    if st.confirm:
        cv.add(f" {_confirm_question(st.confirm)} y/n", "alert")
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
    "  Keys: ↑↓ select  d default  ←→ volume  Space mute  Enter label  p profile",
    "        t test  h hide  H show hidden  m monitors  a add bus  x delete bus",
    "        v preview  A apply  R restart WirePlumber  Tab/1-4 tabs  q quit",
]


def _fit(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def _device_line_text(kind, obj, devices, selected):
    prefix = "> " if selected else "  "
    if kind == "card":
        return f"{prefix}{obj.label} · profile {obj.active or '?'}", "title"
    if kind == "virtual":
        return "  Virtual", "title"
    if kind == "pending":
        return f"{prefix}+ duplex {_fit(obj['label'], 30):<30} pending: A creates it  {obj['name']}", "dim"
    c = devices["config"]
    vol = "muted" if obj.mute else "" if obj.volume is None else f"{round(obj.volume * 100)}%"
    pending = c["labels"].get(obj.name)
    hidden = obj.name in c["hidden"]
    tags = [t for t, on in (("pwctl", obj.name in devices["owned"]), ("made outside pwctl", obj.name in devices["clash"]),
                            ("hidden", hidden), (f"label → {pending}", bool(pending) and pending != obj.label)) if on]
    text = (f"{prefix}{'★' if obj.default else ' '} {obj.direction:<6} {_fit(obj.label, 30):<30} "
            f"{obj.state or '?':<9} {vol:<6} {obj.id:>5}" + (f"  [{', '.join(tags)}]" if tags else ""))
    return text, "dim" if hidden else "normal"


def _render_devices(cv, st, devices, body_h):
    if devices is None:
        cv.add("  loading devices…", "dim")
        return
    lines, n_sel = [], 0
    selectable = dev_selectable(devices, st)
    cursor = max(0, min(len(selectable) - 1, st.dev_cursor)) if selectable else -1
    for kind, obj in _dev_lines(devices, st):
        idx = None if kind == "virtual" else n_sel
        text, style = _device_line_text(kind, obj, devices, idx == cursor)
        lines.append((text, "selected" if idx == cursor else style, idx))
        if idx is not None:
            n_sel += 1
    room = max(1, body_h - 3 - len(DEVICE_KEYS))  # blank line, buttons, note and key legend sit below
    at = next((n for n, line in enumerate(lines) if line[1] == "selected"), 0)
    start = 0 if at < room else at - room + 1
    for text, style, idx in lines[start:start + room]:
        row = cv.add(text, style)
        if idx is not None:
            cv.hit(row, 0, cv.width, ("device", idx))
    cv.add("")
    _buttons(cv, [("dev_default", "Default"), ("dev_mute", "Mute"), ("dev_label", "Label"),
                  ("dev_profile", "Profile"), ("dev_add_bus", "Add bus"), ("dev_apply", "Apply")])
    if devices["dirty"]:
        cv.add("  * label or clock changes not written yet; A writes them (no restart)", "alert")
    else:
        cv.add("  generated files match devices.json", "dim")
    for line in DEVICE_KEYS:
        cv.add(line, "dim")
