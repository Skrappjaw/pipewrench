"""Pure logic for the Devices tab: every audio card, sink, source and bus in a pw-dump, with state,
format, volume and defaults. No curses and no subprocess calls here. Plan: docs/devices-v2-plan.md.
"""
import json
import re
from dataclasses import dataclass, field, replace

HW_APIS = ("alsa", "bluez5")
METER_NAME = "pipewrench-meter"  # PipeWrench's own level-meter streams; never listed anywhere
METER_RATE = 3000  # Hz: peaks within ~3% of full rate in the spike (docs/mixer-plan.md); 1 kHz blunts them
DUMP_CMD = ["pw-dump"]


class InventoryError(Exception):
    pass


def read_dump(run):
    rc, out = run(DUMP_CMD)
    if rc != 0:
        raise InventoryError(out.strip() or f"pw-dump exited {rc}")
    return merge_dump(out)


def merge_dump(text):
    """pw-dump prints a JSON array, plus update arrays if the graph changes while it runs. Apply them
    in order: a later copy of an object replaces the earlier one, and "info": null means removed."""
    decoder, pos, objects = json.JSONDecoder(), 0, {}
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        doc, pos = decoder.raw_decode(text, pos)
        for o in doc if isinstance(doc, list) else [doc]:
            if not isinstance(o, dict) or "id" not in o:
                continue
            if "info" in o and o["info"] is None:
                objects.pop(o["id"], None)
            else:
                objects[o["id"]] = o
    return list(objects.values())

_DIRECTION = {"Audio/Sink": "out", "Audio/Source": "in", "Audio/Duplex": "duplex"}
_DIR_ORDER = {"out": 0, "in": 1, "duplex": 2}


@dataclass(frozen=True)
class Profile:
    index: int
    name: str
    description: str


@dataclass(frozen=True)
class Row:
    id: int
    name: str
    label: str
    direction: str            # out | in | duplex
    kind: str                 # hardware | virtual | monitor | app (Mixer only)
    card_id: int = None
    state: str = None
    format: str = None
    rate: int = None
    channels: int = None
    volume: float = None      # wpctl's cubic scale, 1.0 = 100%
    mute: bool = None
    default: bool = False
    inputs: tuple = ()        # who plays into it (apps or devices)
    outputs: tuple = ()       # who reads from it: recorders, or an output's monitor
    serial: int = None        # object.serial, what pw-record --target takes
    pid: int = None           # application.process.id (app streams), to find a media player's stream


@dataclass
class Card:
    id: int
    name: str
    label: str
    profiles: list
    active: str
    rows: list = field(default_factory=list)


@dataclass
class Inventory:
    cards: list
    virtual: list
    defaults: dict

    def rows(self):
        return [r for c in self.cards for r in c.rows] + list(self.virtual)

    def find(self, key):
        """A row by node id, exact node.name, or a label matching exactly one row (any case)."""
        rows = self.rows()
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            return next((r for r in rows if r.id == int(key)), None)
        exact = next((r for r in rows if r.name == key), None)
        if exact:
            return exact
        hits = [r for r in rows if r.label.lower() == str(key).lower()]
        if len(hits) > 1:
            raise LookupError(f"{key!r} matches {len(hits)} devices: " + ", ".join(f"{r.id} ({r.direction})" for r in hits))
        return hits[0] if hits else None

    def find_card(self, key):
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            return next((c for c in self.cards if c.id == int(key)), None)
        return next((c for c in self.cards if c.name == key or c.label.lower() == str(key).lower()), None)


def natural_key(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def _info(obj):
    return (obj or {}).get("info") or {}


def _defaults(dump):
    out = {"sink": None, "source": None}
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Metadata" or (o.get("props") or {}).get("metadata.name") != "default":
            continue
        values = {}
        for m in o.get("metadata") or []:
            v = m.get("value")
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    continue
            if isinstance(v, dict) and "name" in v:
                values[m.get("key")] = v["name"]
        for k in ("sink", "source"):
            out[k] = values.get(f"default.configured.audio.{k}") or values.get(f"default.audio.{k}")
    return out


def _row(obj, defaults):
    info = _info(obj)
    p = info.get("props") or {}
    direction = _DIRECTION.get(p.get("media.class"))
    if not direction or not p.get("node.name"):
        return None
    params = info.get("params") or {}
    fmt = next((f for f in params.get("Format") or [] if isinstance(f, dict)), {})
    volume, mute = _volume(info)
    hardware = p.get("device.api") in HW_APIS and p.get("device.id") is not None
    name = p["node.name"]
    is_default = name in (defaults["sink"] if direction != "in" else None, defaults["source"] if direction != "out" else None)
    return Row(id=obj["id"], name=name, label=p.get("node.description") or p.get("node.nick") or name,
               direction=direction, kind="hardware" if hardware else "virtual",
               card_id=p.get("device.id") if hardware else None, state=info.get("state"),
               format=fmt.get("format"), rate=fmt.get("rate") or p.get("audio.rate"),
               channels=fmt.get("channels") or p.get("audio.channels"),
               volume=volume, mute=mute, default=is_default, serial=p.get("object.serial"))


def is_meter(props):
    return str(props.get("node.name", "")).startswith(METER_NAME)


def meter_cmd(serial, monitor):
    """A pw-record that streams a node's audio as raw stereo f32 on stdout; monitor reads a sink's output.
    stdbuf -o0: into a pipe, pw-record otherwise holds 4 KB back, which at 3 kHz is a write every 170 ms."""
    props = "{ node.name = " + METER_NAME + ", node.dont-reconnect = true" + (", stream.capture.sink = true" if monitor else "") + " }"
    return ["stdbuf", "-o0", "pw-record", "--target", str(serial), "--rate", str(METER_RATE), "--channels", "2", "--format", "f32",
            "-P", props, "-"]


def _volume(info):
    """(volume on wpctl's cubic scale, mute) from a node's Props; (None, None) when it has no control."""
    props = next((x for x in (info.get("params") or {}).get("Props") or []
                  if isinstance(x, dict) and "channelVolumes" in x), {})
    vols = props.get("channelVolumes")
    return (round(max(vols) ** (1 / 3), 4) if vols else None), (props.get("mute") if props else None)


_STREAM_DIRECTION = {"Stream/Output/Audio": "out", "Stream/Input/Audio": "in"}


def streams(dump):
    """App rows for the Mixer: playback ("out") and recording ("in") streams, plus JACK clients, which
    have no media.class and no volume, listed by the way they're linked. Peak-detect streams are left out."""
    peers = _peers(dump)
    rows = []
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node" or "id" not in o:
            continue
        info = _info(o)
        p = info.get("props") or {}
        if p.get("stream.monitor") in (True, "true") or is_meter(p):
            continue
        cls = p.get("media.class", "")
        ins, outs = peers.get(o["id"], ((), ()))
        if cls in _STREAM_DIRECTION:
            directions = [_STREAM_DIRECTION[cls]]
        elif not cls and p.get("media.type") == "Audio":
            directions = [d for d, linked in (("out", outs), ("in", ins)) if linked] or ["out"]
        else:
            continue
        volume, mute = _volume(info)
        rows += [Row(id=o["id"], name=p.get("node.name", str(o["id"])), label=display_name(p), direction=d,
                     kind="app", state=info.get("state"), volume=volume, mute=mute, inputs=ins, outputs=outs,
                     serial=p.get("object.serial"), pid=p.get("application.process.id"))
                 for d in directions]
    return sorted(rows, key=lambda r: natural_key(r.label))


def _card(obj):
    info = _info(obj)
    p = info.get("props") or {}
    params = info.get("params") or {}
    profiles = [Profile(x.get("index"), x.get("name", ""), x.get("description", ""))
                for x in params.get("EnumProfile") or [] if isinstance(x, dict)]
    active = next((x.get("name") for x in params.get("Profile") or [] if isinstance(x, dict)), None)
    return Card(id=obj["id"], name=p.get("device.name", str(obj["id"])),
                label=p.get("device.description") or p.get("device.nick") or p.get("device.name", str(obj["id"])),
                profiles=profiles, active=active)


def _sort(rows):
    return sorted(rows, key=lambda r: (_DIR_ORDER[r.direction], natural_key(r.name)))


_INTERPRETERS = re.compile(r"^(python[\d.]*|node|java|sh|bash|perl|ruby|wine.*)$")


# media.name values that say nothing about the stream (Brave "Playback", Discord "recStream").
_GENERIC_MEDIA = re.compile(r"(?i)^((alsa )?(playback|capture|record(ing)?|output|input|audio)( stream)?"
                            r"|(play|rec|audio) ?stream|stream)$")


def display_name(props):
    """What to call a node: an app by its process when that's clearer than the name its audio library
    gives (Discord rather than 'WEBRTC VoiceEngine'), else its description. An app stream adds its own
    media.name when that says something, so OBS's four inputs read OBS · Desktop Audio, OBS · Mic/Aux…"""
    app, binary = props.get("application.name"), props.get("application.process.binary")
    if app and binary and binary.lower() not in app.lower() and not _INTERPRETERS.match(binary):
        name = binary
    else:
        name = app or props.get("node.description") or props.get("node.nick") or props.get("node.name") or "?"
    media = (props.get("media.name") or "").strip()
    if str(props.get("media.class", "")).startswith("Stream/") and media and not _GENERIC_MEDIA.match(media) \
            and media.lower() not in (name.lower(), str(app).lower()):
        return f"{name} · {media}"
    return name


_peer_name = display_name


def _peers(dump):
    """{node id: (inputs, outputs)} from the link objects, one name per peer, error links skipped."""
    names = {o["id"]: _peer_name(_info(o).get("props") or {}) for o in dump
             if o.get("type") == "PipeWire:Interface:Node" and "id" in o and not is_meter(_info(o).get("props") or {})}
    ins, outs = {}, {}
    for o in dump:
        info = _info(o)
        if o.get("type") != "PipeWire:Interface:Link" or info.get("state") == "error":
            continue
        a, b = info.get("output-node-id"), info.get("input-node-id")
        if a in names and b in names:
            ins.setdefault(b, set()).add(names[a])
            outs.setdefault(a, set()).add(names[b])
    order = lambda s: tuple(sorted(s, key=str.lower))
    return {n: (order(ins.get(n, ())), order(outs.get(n, ()))) for n in set(ins) | set(outs)}


def inventory(dump):
    defaults = _defaults(dump)
    peers = _peers(dump)
    cards = {o["id"]: _card(o) for o in dump
             if o.get("type") == "PipeWire:Interface:Device" and (_info(o).get("props") or {}).get("media.class") == "Audio/Device"}
    virtual = []
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node" or "id" not in o:
            continue
        r = _row(o, defaults)
        if r is None:
            continue
        if r.id in peers:
            r = replace(r, inputs=peers[r.id][0], outputs=peers[r.id][1])
        if r.card_id in cards:
            cards[r.card_id].rows.append(r)
        else:
            virtual.append(r)
    for c in cards.values():
        c.rows = _sort(c.rows)
    return Inventory(cards=sorted(cards.values(), key=lambda c: c.label.lower()),
                     virtual=sorted(virtual, key=lambda r: natural_key(r.name)), defaults=defaults)


def monitor_rows(rows):
    """What capture apps see as '<sink>.monitor': one input per output."""
    return [replace(r, id=r.id, name=f"{r.name}.monitor", label=f"{r.label} (monitor)", direction="in",
                    kind="monitor", default=False, inputs=())
            for r in rows if r.direction == "out"]


def connections(row):
    """What a device is connected to: "← apps playing into it  → apps reading from it"."""
    parts = []
    if row.inputs:
        parts.append("← " + ", ".join(row.inputs))
    if row.outputs:
        monitor = row.direction == "out" and row.kind != "monitor"
        parts.append("→ " + ", ".join(row.outputs) + (" (monitor)" if monitor else ""))
    return "  ".join(parts)


def view(inv, hidden=(), show_hidden=False, monitors=False):
    """Lines for the Devices tab: (heading, None) for a group, ("", row) for a device."""
    hidden = set(hidden)
    out = []

    def rows_of(rows):
        rows = list(rows) + (monitor_rows(rows) if monitors else [])
        return [("", r) for r in rows if show_hidden or r.name not in hidden]

    off = []  # cards on profile off carry no devices; they wait at the bottom
    for c in inv.cards:
        if c.name in hidden and not show_hidden:
            continue
        if c.active == "off":
            off.append((f"{c.label} · off", None))
            continue
        out.append((f"{c.label} · profile {c.active or '?'}", None))
        out += rows_of(c.rows)
    out.append(("Virtual", None))
    out += rows_of(inv.virtual)
    return out + ([("Off", None)] + off if off else [])


# --- commands (wpctl) -------------------------------------------------------------

_REL = re.compile(r"([+-])(\d{1,3})%")
_ABS_PCT = re.compile(r"(\d{1,3})%")
_ABS_FRAC = re.compile(r"\d(\.\d+)?")
MAX_VOLUME = 1.5  # wpctl's own ceiling for set-volume


def _actionable(row):
    if row.kind == "monitor":
        raise ValueError(f"{row.label} is a monitor; act on its sink instead")


def default_cmd(row):
    _actionable(row)
    return ["wpctl", "set-default", str(row.id)]


def mute_cmd(row):
    _actionable(row)
    return ["wpctl", "set-mute", str(row.id), "toggle"]


def volume_cmd(row, step):
    """step: +5% / -5% (relative), 50% or 0.5 (absolute, up to 150%)."""
    _actionable(row)
    step = str(step).strip()
    if m := _REL.fullmatch(step):
        return ["wpctl", "set-volume", str(row.id), f"{m.group(2)}%{m.group(1)}"]
    if (m := _ABS_PCT.fullmatch(step)) and int(m.group(1)) <= MAX_VOLUME * 100:
        return ["wpctl", "set-volume", str(row.id), step]
    if _ABS_FRAC.fullmatch(step) and float(step) <= MAX_VOLUME:
        return ["wpctl", "set-volume", str(row.id), step]
    raise ValueError(f"volume {step!r}: use +5%, -5%, 50% or 0.5 (at most 150%)")


def profile_cmd(card, profile):
    """profile: an index or a name from the card's profile list."""
    p = next((p for p in card.profiles if str(p.index) == str(profile) or p.name == profile), None)
    if p is None:
        names = ", ".join(p.name for p in card.profiles)
        raise ValueError(f"{card.label} has no profile {profile!r}; choose one of: {names}")
    return ["wpctl", "set-profile", str(card.id), str(p.index)]


def bus_create_cmd(name, label):
    """A stereo bus made live: it lingers after pw-cli exits and lasts until destroyed or PipeWire restarts."""
    props = (f'factory.name=support.null-audio-sink node.name="{name}" node.description="{label}" '
             "media.class=Audio/Duplex object.linger=true audio.position=[FL FR] monitor.channel-volumes=true")
    return ["pw-cli", "create-node", "adapter", "{ " + props + " }"]


def destroy_cmd(row):
    if row.kind != "virtual":
        raise ValueError(f"{row.label} is hardware; switch its card profile instead")
    return ["pw-cli", "destroy", str(row.id)]
