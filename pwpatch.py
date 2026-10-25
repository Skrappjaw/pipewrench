"""Pure logic for the Patch tab: sources, destinations and their ports from a pw-dump, connection
state per stereo pair, and the pw-link commands that connect or disconnect them. MIDI ports are listed
too, after the audio, and only ever link to MIDI. Runtime links only; saved patch sets belong to scenes. No curses and no subprocess calls here. Plan: notes/patchbay-plan.md.
"""
import re
from dataclasses import dataclass

import pwinventory as pi

_CHANNELS = ["MONO", "FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR", "FLC", "FRC", "RC", "TC"]
_KIND_ORDER = {"app": 0, "device": 1, "bus": 2}


@dataclass(frozen=True)
class Port:
    id: int
    node: int
    name: str
    channel: str
    direction: str            # output | input
    monitor: bool = False


@dataclass(frozen=True)
class Endpoint:
    node: int
    monitor: bool
    label: str
    name: str                 # node.name
    kind: str                 # app | device | bus
    ports: tuple
    media: str = "audio"      # audio | midi; the two never link


def _channel_key(channel):
    if channel in _CHANNELS:
        return (0, _CHANNELS.index(channel))
    return (1, pi.natural_key(channel))


def pair_ports(src, dst):
    """Which output port feeds which input: by channel name (FL→FL), mono to every channel and every
    channel to a mono input, else by position."""
    src, dst = list(src), list(dst)
    if not src or not dst:
        return []
    if len(dst) == 1:
        return [(s, dst[0]) for s in src]
    if len(src) == 1:
        return [(src[0], d) for d in dst]
    by_channel = {d.channel: d for d in dst}
    matched = [(s, by_channel[s.channel]) for s in src if s.channel in by_channel]
    return matched if len(matched) == len(src) else list(zip(src, dst))


def slots(s, d):
    """Where a source can land on a destination whose inputs have no channel names (REAPER's in1…in8,
    AUXn): aligned runs of as many inputs as the source has outputs. None when there's no choice."""
    k, n = len(s.ports), len(d.ports)
    if s.media != d.media or not k or n <= k or any(p.channel in _CHANNELS for p in d.ports):
        return None
    return [d.ports[i:i + k] for i in range(0, n - k + 1, k)]


class Graph:
    def __init__(self, sources, dests, links):
        self.sources, self.dests = sources, dests
        self.links = links    # {(output port id, input port id): link id}

    def _any_link(self, s, d):
        return any((a.id, b.id) in self.links for a in s.ports for b in d.ports)

    def state(self, s, d):
        """full: every paired channel linked (into any one slot); partial: some link between them; none;
        n/a between audio and MIDI."""
        if s.media != d.media:
            return "n/a"
        runs = slots(s, d)
        for pairs in ([list(zip(s.ports, r)) for r in runs] if runs else [pair_ports(s.ports, d.ports)]):
            if pairs and all((a.id, b.id) in self.links for a, b in pairs):
                return "full"
        return "partial" if self._any_link(s, d) else "none"

    def slot_sources(self, slot):
        """The sources linked into any of a slot's inputs."""
        feeding = {a for a, b in self.links if b in {p.id for p in slot}}
        return [s for s in self.sources if any(p.id in feeding for p in s.ports)]

    def first_free(self, runs):
        return next((k for k, r in enumerate(runs) if not self.slot_sources(r)), 0)

    def targets(self, s):
        return [d for d in self.dests if self._any_link(s, d)]

    @staticmethod
    def _find(items, key, media=None):
        """By node id (audio only: every ALSA MIDI port shares the bridge's node), exact name (a MIDI
        port's alias), or a label matching one endpoint; media narrows it to audio or MIDI."""
        items = [e for e in items if media in (None, e.media)]
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            return next((e for e in items if e.node == int(key) and not e.monitor and e.media == "audio"), None)
        exact = next((e for e in items if e.name == key and not e.monitor), None)
        if exact:
            return exact
        hits = [e for e in items if e.label.lower() == str(key).lower()]
        if len(hits) > 1:
            raise LookupError(f"{key!r} matches {len(hits)} endpoints: "
                              + ", ".join(e.name if e.media == "midi" else str(e.node) for e in hits))
        return hits[0] if hits else None

    def find_source(self, key, media=None):
        return self._find(self.sources, key, media)

    def find_dest(self, key, media=None):
        return self._find(self.dests, key, media)


def _is_audio_node(props):
    """Devices, buses and app streams carry a media.class; JACK clients (pw-jack) only a media.type."""
    cls = props.get("media.class", "")
    return cls.startswith("Audio/") or (cls.startswith("Stream/") and "Audio" in cls) \
        or (not cls and props.get("media.type") == "Audio")


def _kind(props):
    cls = props.get("media.class", "")
    if cls.startswith("Stream/") or not cls:
        return "app"
    return "device" if props.get("device.api") in pi.HW_APIS else "bus"


def _is_midi(port_props):
    dsp = str(port_props.get("format.dsp", "")).lower()
    return "midi" in dsp or "ump" in dsp


_MIDI_THROUGH = "Midi Through:"  # the kernel's loopback port; never something to patch


def _midi(dump, links):
    """(sources, destinations): one endpoint per MIDI port. ALSA MIDI ports all hang off one Midi-Bridge
    node, so each is named by its alias (GCS-8:GCS-8 MIDI 1 → GCS-8 MIDI 1). A JACK client's port
    (REAPER) takes the client's name, plus the port's when it has several one way."""
    nodes = {o["id"]: ((o.get("info") or {}).get("props")) or {} for o in dump
             if o.get("type") == "PipeWire:Interface:Node" and "id" in o}
    found = []
    for o in dump:
        info = o.get("info") or {}
        p = info.get("props") or {}
        node = nodes.get(p.get("node.id"))
        if o.get("type") != "PipeWire:Interface:Port" or node is None or pi.is_meter(node) or not _is_midi(p):
            continue
        alias = p.get("port.alias") or p.get("port.name", str(o["id"]))
        if alias.startswith(_MIDI_THROUGH) or info.get("direction") not in ("output", "input"):
            continue
        found.append((Port(o["id"], p["node.id"], p.get("port.name", ""), "MIDI", info["direction"]), alias, node))
    per_client = {}
    for port, _, node in found:
        per_client[(port.node, port.direction)] = per_client.get((port.node, port.direction), 0) + 1
    sources, dests = [], []
    for port, alias, node in found:
        if node.get("media.class") == "Midi/Bridge":
            label, kind = alias.split(":", 1)[-1].strip(), "device"
        else:
            label, kind = pi.display_name(node), "app"
            if per_client[(port.node, port.direction)] > 1:
                label = f"{label} · {port.name}"
        e = Endpoint(port.node, False, label, alias, kind, (port,), "midi")
        (sources if port.direction == "output" else dests).append(e)
    order = lambda e: (_KIND_ORDER[e.kind], pi.natural_key(e.label))
    return sorted(sources, key=order), sorted(dests, key=order)


def graph(dump):
    nodes = {}
    for o in dump:
        p = ((o.get("info") or {}).get("props")) or {}
        if o.get("type") == "PipeWire:Interface:Node" and "id" in o and _is_audio_node(p) and not pi.is_meter(p):
            nodes[o["id"]] = p
    ports = []
    for o in dump:
        info = o.get("info") or {}
        p = info.get("props") or {}
        if o.get("type") != "PipeWire:Interface:Port" or p.get("node.id") not in nodes:
            continue
        if "audio" not in str(p.get("format.dsp", "audio")).lower():
            continue  # a JACK client's MIDI port: _midi lists it
        name = p.get("port.name", "")
        channel = p.get("audio.channel") or name.rsplit("_", 1)[-1]
        ports.append(Port(o["id"], p["node.id"], name, channel, info.get("direction"),
                          p.get("port.monitor") in (True, "true")))
    links = {}
    for o in dump:
        info = o.get("info") or {}
        if o.get("type") == "PipeWire:Interface:Link" and info.get("state") != "error" and "id" in o:
            links[(info.get("output-port-id"), info.get("input-port-id"))] = o["id"]

    def endpoint(node, monitor, plist):
        p = nodes[node]
        label = pi.display_name(p) + (" (monitor)" if monitor else "")
        return Endpoint(node, monitor, label, p.get("node.name", str(node)), _kind(p),
                        tuple(sorted(plist, key=lambda x: _channel_key(x.channel))))

    groups_out, groups_in = {}, {}
    for port in ports:
        if port.direction == "output":
            groups_out.setdefault((port.node, port.monitor), []).append(port)
        elif port.direction == "input":
            groups_in.setdefault(port.node, []).append(port)
    sources = [endpoint(n, m, pl) for (n, m), pl in groups_out.items()]
    sources.sort(key=lambda e: (e.monitor, _KIND_ORDER[e.kind], pi.natural_key(e.label)))
    dests = [endpoint(n, False, pl) for n, pl in groups_in.items()]
    dests.sort(key=lambda e: (_KIND_ORDER[e.kind], pi.natural_key(e.label)))
    midi_src, midi_dst = _midi(dump, links)
    return Graph(sources + midi_src, dests + midi_dst, links)


# --- commands (pw-link) ----------------------------------------------------------------

def connect_cmds(g, s, d):
    if s.media != d.media:
        return []
    return [["pw-link", str(a.id), str(b.id)] for a, b in pair_ports(s.ports, d.ports)
            if (a.id, b.id) not in g.links]


def disconnect_cmds(g, s, d):
    return [["pw-link", "-d", str(g.links[(a.id, b.id)])]
            for a in s.ports for b in d.ports if (a.id, b.id) in g.links]


def toggle_cmds(g, s, d):
    return disconnect_cmds(g, s, d) if g.state(s, d) != "none" else connect_cmds(g, s, d)


def slot_cmds(g, s, slot):
    """Toggle a source on one slot: drop its links into those inputs, else link it in by position."""
    drop = [["pw-link", "-d", str(g.links[(a.id, b.id)])] for a in s.ports for b in slot if (a.id, b.id) in g.links]
    return drop or [["pw-link", str(a.id), str(b.id)] for a, b in zip(s.ports, slot)]


def port_pairs(g, s, d):
    """Every output port × input port between two endpoints, with whether it's linked."""
    return [(a, b, (a.id, b.id) in g.links) for a in s.ports for b in d.ports]


def port_toggle_cmd(g, a, b):
    if (a.id, b.id) in g.links:
        return ["pw-link", "-d", str(g.links[(a.id, b.id)])]
    return ["pw-link", str(a.id), str(b.id)]
