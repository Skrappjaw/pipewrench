"""Pure logic for the Devices tab: every audio card, sink, source and bus in a pw-dump, with state,
format, volume and defaults. No curses and no subprocess calls here. Plan: docs/devices-v2-plan.md.
"""
import json
import re
from dataclasses import dataclass, field, replace

HW_APIS = ("alsa", "bluez5")
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
    kind: str                 # hardware | virtual | monitor
    card_id: int = None
    state: str = None
    format: str = None
    rate: int = None
    channels: int = None
    volume: float = None      # wpctl's cubic scale, 1.0 = 100%
    mute: bool = None
    default: bool = False


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
    props = next((x for x in params.get("Props") or [] if isinstance(x, dict) and "channelVolumes" in x), {})
    vols = props.get("channelVolumes")
    hardware = p.get("device.api") in HW_APIS and p.get("device.id") is not None
    name = p["node.name"]
    is_default = name in (defaults["sink"] if direction != "in" else None, defaults["source"] if direction != "out" else None)
    return Row(id=obj["id"], name=name, label=p.get("node.description") or p.get("node.nick") or name,
               direction=direction, kind="hardware" if hardware else "virtual",
               card_id=p.get("device.id") if hardware else None, state=info.get("state"),
               format=fmt.get("format"), rate=fmt.get("rate") or p.get("audio.rate"),
               channels=fmt.get("channels") or p.get("audio.channels"),
               volume=round(max(vols) ** (1 / 3), 4) if vols else None,
               mute=props.get("mute") if props else None, default=is_default)


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


def inventory(dump):
    defaults = _defaults(dump)
    cards = {o["id"]: _card(o) for o in dump
             if o.get("type") == "PipeWire:Interface:Device" and (_info(o).get("props") or {}).get("media.class") == "Audio/Device"}
    virtual = []
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node" or "id" not in o:
            continue
        r = _row(o, defaults)
        if r is None:
            continue
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
                    kind="monitor", default=False)
            for r in rows if r.direction == "out"]


def view(inv, hidden=(), show_hidden=False, monitors=False):
    """Lines for the Devices tab: (heading, None) for a group, ("", row) for a device."""
    hidden = set(hidden)
    out = []

    def rows_of(rows):
        rows = list(rows) + (monitor_rows(rows) if monitors else [])
        return [("", r) for r in rows if show_hidden or r.name not in hidden]

    for c in inv.cards:
        if c.name in hidden and not show_hidden:
            continue
        out.append((f"{c.label} · profile {c.active or '?'}", None))
        out += rows_of(c.rows)
    out.append(("Virtual", None))
    out += rows_of(inv.virtual)
    return out
