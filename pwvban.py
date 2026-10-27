"""Pure logic for VBAN network streams (Settings → Network): vban.json, the generated PipeWire configs and
systemd unit that run each stream as its own process, VBAN packet headers, and the start/stop plan.
No curses, sockets or subprocess calls here; callers pass a runner. Plan: notes/vban-plan.md.

PipeWire ships the protocol (libpipewire-module-vban-send / -recv, since 0.3.76). Each stream runs as
`pipewire -c <conf>` under the user unit pipewrench-vban@<instance>, so turning one on or off never restarts
PipeWire. Before 1.4.2 a listener makes one stream per port and can't tell senders apart, so each incoming
stream gets its own port; from 1.4.2 one listener per port matches senders by IP (stream.rules).
"""
import ipaddress
import json
import os
import re
from collections import namedtuple
from pathlib import Path

import pwconfig as pcfg
import pwpatch as pp

PORT = 6980  # VBAN's standard port
RULES_SINCE = (1, 4, 2)  # the first release seen with vban-recv stream.rules (Debian trixie, 2026-10-26)
MEDIA = ("audio", "midi")
FORMATS = ("U8", "S16LE", "S24LE", "S32LE", "F32LE", "F64LE")
# Format… choices: (label, (rate, format, channels)) or "midi" (Skrappjaw, 2026-10-26: basic set, MIDI included)
FORMAT_CHOICES = [("48 kHz · 16-bit · stereo", (48000, "S16LE", 2)), ("48 kHz · 16-bit · mono", (48000, "S16LE", 1)),
                  ("44.1 kHz · 16-bit · stereo", (44100, "S16LE", 2)), ("48 kHz · 24-bit · stereo", (48000, "S24LE", 2)),
                  ("48 kHz · 32-bit float · stereo", (48000, "F32LE", 2)), ("MIDI", "midi")]
UNIT_PREFIX = "pipewrench-vban@"
STREAM_MAX = 16  # VBAN stream names are 16 bytes

# VBAN header (VB-Audio spec; checked against the Studio's packets, 2026-10-26): "VBAN", then
# byte 4 = sub-protocol (top 3 bits) | rate index (low 5); 5 = samples - 1; 6 = channels - 1; 7 = data type;
# 8..23 = stream name; 24..27 = frame counter.
RATES = [6000, 12000, 24000, 48000, 96000, 192000, 384000, 8000, 16000, 32000, 64000, 128000, 256000, 512000,
         11025, 22050, 44100, 88200, 176400, 352800, 705600]
_AUDIO, _SERIAL = 0x00, 0x20
_DATA_TYPES = {0: "U8", 1: "S16LE", 2: "S24LE", 3: "S32LE", 4: "F32LE", 5: "F64LE"}

Heard = namedtuple("Heard", "stream media rate channels format", defaults=(None, None, None))
Plan = namedtuple("Plan", "write remove start restart stop")


# --- headers ----------------------------------------------------------------------

def parse_header(packet):
    """A Heard for an audio or MIDI (serial) VBAN packet; None for anything else, VBAN-TEXT included."""
    if len(packet) < 28 or packet[:4] != b"VBAN":
        return None
    proto, index = packet[4] & 0xE0, packet[4] & 0x1F
    stream = packet[8:24].split(b"\0", 1)[0].decode("utf-8", "replace")
    if proto == _SERIAL:
        return Heard(stream, "midi")
    if proto != _AUDIO or index >= len(RATES) or (packet[7] & 0x07) not in _DATA_TYPES:
        return None
    return Heard(stream, "audio", RATES[index], packet[6] + 1, _DATA_TYPES[packet[7] & 0x07])


def heard_label(h, ip):
    what = "MIDI" if h.media == "midi" else f"{h.rate / 1000:g} kHz · {h.channels} ch"
    return f"{h.stream} · {ip} · {what}"


# --- versions -------------------------------------------------------------------

def parse_version(text):
    """(major, minor, micro) from `pipewire --version`: the library it's linked with."""
    m = re.search(r"Linked with libpipewire (\d+)\.(\d+)\.(\d+)", text) or re.search(r"libpipewire (\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def supports_rules(version):
    return bool(version) and tuple(version) >= RULES_SINCE


# --- vban.json ------------------------------------------------------------------

def default_config():
    return {"version": 1, "incoming": [], "outgoing": []}


def config_path():
    return pcfg.app_dir() / "vban.json"


def conf_dir():
    return pcfg.app_dir() / "vban"


def unit_path():
    return pcfg._config_home() / "systemd" / "user" / f"{UNIT_PREFIX}.service"


def _key(direction):
    if direction not in ("in", "out"):
        raise ValueError(f"direction must be in or out, not {direction!r}")
    return "incoming" if direction == "in" else "outgoing"


def check_ip(ip):
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        raise ValueError(f"{ip!r} is not an IPv4 address (like 192.168.1.20)") from None
    if addr.version != 4:
        raise ValueError(f"{ip!r}: VBAN here is IPv4 only")
    return str(addr)


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "stream"


def new_entry(direction, name, ip):
    """A stream with the defaults most senders use (48 kHz, 16-bit, stereo: Voicemeeter's too)."""
    _key(direction)
    pcfg._check_text(name, "name")
    name = name.strip()
    return {"id": _slug(name), "name": name, "ip": check_ip(ip), "port": PORT, "stream": name[:STREAM_MAX],
            "media": "audio", "rate": 48000, "format": "S16LE", "channels": 2, "target": None, "on": True, "latency": 40}


def from_heard(direction, heard, ip):
    e = {**new_entry(direction, heard.stream or ip, ip), "stream": (heard.stream or "")[:STREAM_MAX], "media": heard.media}
    if heard.media == "audio":
        e.update(rate=heard.rate, channels=heard.channels, format=heard.format)
    return e


def validate_entry(e):
    want = {"id": str, "name": str, "ip": str, "port": int, "stream": str, "media": str, "rate": int, "format": str,
            "channels": int, "on": bool, "latency": int}
    for k, t in want.items():
        if not isinstance(e.get(k), t):
            raise ValueError(f"vban.json: stream {e.get('id')!r}: {k} missing or not {t.__name__}")
    if not re.fullmatch(r"[a-z0-9-]{1,40}", e["id"]):
        raise ValueError(f"vban.json: bad id {e['id']!r}")
    pcfg._check_text(e["name"], "name")
    check_ip(e["ip"])
    if e["media"] not in MEDIA or e["format"] not in FORMATS or e["rate"] not in RATES:
        raise ValueError(f"vban.json: stream {e['id']!r}: media, format or rate not allowed")
    if not (1 <= e["port"] <= 65535 and 1 <= e["channels"] <= 64 and 0 < e["latency"] <= 1000):
        raise ValueError(f"vban.json: stream {e['id']!r}: port, channels or latency out of range")
    if e.get("target") is not None:
        pcfg._check_text(e["target"], "target")
    if '"' in e["stream"] or "\\" in e["stream"] or len(e["stream"]) > STREAM_MAX:
        raise ValueError(f"vban.json: stream {e['id']!r}: bad stream name")


def validate_config(c):
    if not isinstance(c, dict) or c.get("version") != 1:
        raise ValueError("vban.json: version 1 expected")
    for key in ("incoming", "outgoing"):
        if not isinstance(c.get(key), list):
            raise ValueError(f"vban.json: {key} must be a list")
        for e in c[key]:
            validate_entry(e)
        ids = [e["id"] for e in c[key]]
        if len(set(ids)) != len(ids):
            raise ValueError(f"vban.json: duplicate {key} ids")


def load_config(path):
    path = Path(path)
    if not path.exists():
        return default_config()
    c = json.loads(path.read_text())
    validate_config(c)
    return c


def save_config(path, c):
    validate_config(c)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(c, indent=2) + "\n")
    os.replace(tmp, path)


def find(c, direction, key):
    """A stream by id or by name (case-insensitive)."""
    for e in c[_key(direction)]:
        if e["id"] == key or e["name"].lower() == str(key).lower():
            return e
    raise LookupError(f"no {'incoming' if direction == 'in' else 'outgoing'} stream {key!r}")


def _copy(c):
    return json.loads(json.dumps(c))


def add_entry(c, direction, e, rules):
    """Add a stream with a unique id. Before 1.4.2 an incoming stream takes the next free port from 6980."""
    c = _copy(c)
    rows = c[_key(direction)]
    ids = {x["id"] for x in rows}
    base, n, e = e["id"], 2, dict(e)
    while e["id"] in ids:
        e["id"], n = f"{base}-{n}", n + 1
    if direction == "in" and not rules:
        used = {x["port"] for x in rows}
        e["port"] = next(p for p in range(PORT, PORT + 1000) if p not in used)
    rows.append(e)
    validate_config(c)
    return c


def update_entry(c, direction, key, **changes):
    c = _copy(c)
    e = find(c, direction, key)
    e.update(changes)
    validate_config(c)
    return c


def set_format(c, direction, key, choice):
    """A FORMAT_CHOICES value: "midi", or (rate, format, channels) for audio."""
    if choice == "midi":
        return update_entry(c, direction, key, media="midi")
    rate, fmt, channels = choice
    return update_entry(c, direction, key, media="audio", rate=rate, format=fmt, channels=channels)


def remove_entry(c, direction, key):
    c = _copy(c)
    e = find(c, direction, key)
    c[_key(direction)] = [x for x in c[_key(direction)] if x["id"] != e["id"]]
    return c


def format_label(e):
    if e["media"] == "midi":
        return "MIDI"
    for label, value in FORMAT_CHOICES:
        if value == (e["rate"], e["format"], e["channels"]):
            return label
    return f"{e['rate'] / 1000:g} kHz · {e['format']} · {e['channels']} ch"


def short_format(e):
    return "MIDI" if e["media"] == "midi" else f"{e['rate'] / 1000:g}k · {e['channels']}ch"


# --- generated files --------------------------------------------------------------

def node_name(direction, entry_id):
    return f"pipewrench-vban-{direction}-{entry_id}"


def instance_of(direction, e, rules):
    """The unit instance that runs this stream: one per stream, except shared listeners (one per port) from 1.4.2."""
    return f"in-{e['port']}" if direction == "in" and rules else f"{direction}-{e['id']}"


def unit(instance):
    return f"{UNIT_PREFIX}{instance}.service"


def _position(channels):
    return "[ MONO ]" if channels == 1 else "[ FL FR ]" if channels == 2 else \
        "[ " + " ".join(f"AUX{i}" for i in range(channels)) + " ]"


def _audio_props(e, pad):
    if e["media"] == "midi":
        return [f'{pad}sess.media = "midi"']
    return [f'{pad}audio.format = "{e["format"]}"', f"{pad}audio.rate = {e['rate']}",
            f"{pad}audio.channels = {e['channels']}", f"{pad}audio.position = {_position(e['channels'])}"]


def _in_props(e, pad):
    props = [f'{pad}node.name = "{node_name("in", e["id"])}"', f'{pad}node.description = "{e["name"]}"',
             f'{pad}media.name = "{e["name"]}"']
    if e.get("target") and e["media"] == "audio":  # WirePlumber links it each time it starts
        props += [f'{pad}target.object = "{e["target"]}"', f"{pad}node.autoconnect = true"]
    else:  # unconnected until patched (Skrappjaw, 2026-10-26); MIDI targets are ports, linked by link_cmds
        props.append(f"{pad}node.autoconnect = false")
    return props


_HEAD = """# {mark}: {what}. Edit it in pwrench (Settings → Network), not here.
context.properties = {{ log.level = 0 }}
context.spa-libs = {{
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}}
context.modules = [
    {{ name = libpipewire-module-rt args = {{ }} flags = [ ifexists nofail ] }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
"""


def render_listener(entries, rules):
    """One vban-recv. Before 1.4.2: one entry, its format and target in stream.props. With rules: every entry on
    this port, each matched by the sender's IP (and stream name when one was heard)."""
    e0 = entries[0]
    lines = [_HEAD.format(mark=pcfg.MARK, what=f"VBAN listener on port {e0['port']}"),
             "    { name = libpipewire-module-vban-recv", "      args = {",
             '        source.ip = "0.0.0.0"', f"        source.port = {e0['port']}",
             f"        sess.latency.msec = {e0['latency']}"]
    if not rules:
        lines += _audio_props(e0, "        ")
        lines += ["        stream.props = {"] + _in_props(e0, "          ") + ["        }"]
    else:
        lines.append("        stream.rules = [")
        for e in entries:
            match = [f'vban.ip = "{e["ip"]}"'] + ([f'sess.name = "{e["stream"]}"'] if e["stream"] else [])
            lines += ["          { matches = [ { " + " ".join(match) + " } ]",
                      "            actions = { create-stream = {",
                      f"              sess.latency.msec = {e['latency']}"]
            lines += _audio_props(e, "              ")
            lines += ["              stream.props = {"] + _in_props(e, "                ") + ["              }",
                      "            } }", "          }"]
        lines.append("        ]")
    lines += ["      }", "    }", "]", ""]
    return "\n".join(lines)


def render_sender(e):
    """One vban-send. Audio is an output device you can pick (Skrappjaw, 2026-10-26) at the lowest priority, so
    WirePlumber never makes it the default."""
    lines = [_HEAD.format(mark=pcfg.MARK, what=f"VBAN stream to {e['ip']}:{e['port']}"),
             "    { name = libpipewire-module-vban-send", "      args = {",
             f'        destination.ip = "{e["ip"]}"', f"        destination.port = {e['port']}",
             f'        sess.name = "{e["stream"] or e["name"][:STREAM_MAX]}"']
    lines += _audio_props(e, "        ")
    lines += ["        stream.props = {", f'          node.name = "{node_name("out", e["id"])}"',
              f'          node.description = "{e["name"]}"', f'          media.name = "{e["name"]}"']
    if e["media"] == "audio":
        lines += ['          media.class = "Audio/Sink"', "          priority.session = 1", "          priority.driver = 1"]
    lines += ["        }", "      }", "    }", "]", ""]
    return "\n".join(lines)


def render_unit(pipewire, folder):
    return (f"# {pcfg.MARK}: runs one VBAN stream (Settings → Network). Instances: {UNIT_PREFIX}<stream>.service\n"
            "[Unit]\nDescription=PipeWrench VBAN %i\nAfter=pipewire.service\nPartOf=pipewire.service\n\n"
            f"[Service]\nExecStart={pipewire} -c {folder}/%i.conf\nRestart=on-failure\nRestartSec=2\n\n"
            "[Install]\nWantedBy=pipewire.service\n")


def instances(c, rules):
    """{instance: conf text} for every stream that's on."""
    out = {}
    if rules:
        ports = {}
        for e in c["incoming"]:
            if e["on"]:
                ports.setdefault(e["port"], []).append(e)
        out.update({f"in-{p}": render_listener(es, rules=True) for p, es in ports.items()})
    else:
        out.update({instance_of("in", e, False): render_listener([e], rules=False) for e in c["incoming"] if e["on"]})
    out.update({instance_of("out", e, rules): render_sender(e) for e in c["outgoing"] if e["on"]})
    return out


# --- start / stop -------------------------------------------------------------------

def plan(want, have, active):
    """What to write, remove, start, restart and stop. want/have: {instance: conf text}; active: running instances."""
    write = {i: t for i, t in want.items() if have.get(i) != t}
    gone = sorted(i for i in have if i not in want)
    start = [i for i in want if i not in active]
    restart = [i for i in write if i in active]
    return Plan(write=write, remove=gone, start=start, restart=restart, stop=gone)


def plan_cmds(p):
    sc = ["systemctl", "--user"]
    return ([sc + ["disable", "--now", unit(i)] for i in p.stop] + [sc + ["enable", "--now", unit(i)] for i in p.start]
            + [sc + ["restart", unit(i)] for i in p.restart])


LIST_UNITS = ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", f"{UNIT_PREFIX}*"]
DAEMON_RELOAD = ["systemctl", "--user", "daemon-reload"]


def parse_units(text):
    """{instance: active state} from LIST_UNITS."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].startswith(UNIT_PREFIX) and parts[0].endswith(".service"):
            out[parts[0][len(UNIT_PREFIX):-len(".service")]] = parts[2]
    return out


def status_word(e, units, rules, direction="in"):
    if not e["on"]:
        return "off"
    state = units.get(instance_of(direction, e, rules))
    return {"active": "on", "failed": "failed", "activating": "starting"}.get(state, "stopped")


def point_here(e, address):
    """What the far end of an incoming stream needs."""
    return f"sends to {address}:{e['port']}"


# --- links PipeWrench keeps (outgoing sources, incoming MIDI) -------------------------

MONITOR = "monitor:"  # a source key for a sink's monitor: MONITOR + the sink's node.name


def endpoint_key(ep):
    return (MONITOR + ep.name) if ep.monitor else ep.name


def _endpoint(items, key, media):
    mon = key.startswith(MONITOR)
    name = key[len(MONITOR):] if mon else key
    return next((x for x in items if x.name == name and x.monitor == mon and x.media == media), None)


def endpoints_for(g, direction, media):
    """What a stream can be linked to: playback destinations for incoming, sources for outgoing. PipeWrench's
    own end of the same stream is left out."""
    if g is None:
        return []
    items = g.dests if direction == "in" else g.sources
    mine = node_name("out" if direction == "out" else "in", "")
    return [x for x in items if x.media == media and not x.name.startswith(mine)]


def target_endpoint(g, direction, e):
    if g is None or not e.get("target"):
        return None
    return _endpoint(g.dests if direction == "in" else g.sources, e["target"], e["media"])


def own_endpoint(g, direction, e):
    """The stream's own node in the graph: an incoming stream is a source, an outgoing one a destination."""
    if g is None:
        return None
    name = node_name(direction, e["id"])
    items = g.sources if direction == "in" else g.dests
    return next((x for x in items if x.name == name and not x.monitor), None)


def keeps_links(c):
    """Does any stream need PipeWrench to keep its link (outgoing sources, incoming MIDI)?"""
    return any(e["on"] and e.get("target") for e in c["outgoing"]) or \
        any(e["on"] and e.get("target") and e["media"] == "midi" for e in c["incoming"])


def link_cmds(c, g):
    """pw-link commands for links PipeWrench keeps: every outgoing stream's source, and incoming MIDI targets
    (audio incoming links itself through target.object). Nothing for a stream or target that isn't there."""
    cmds = []
    for direction, key in (("in", "incoming"), ("out", "outgoing")):
        for e in c[key]:
            if not e["on"] or not e.get("target") or (direction == "in" and e["media"] == "audio"):
                continue
            mine, other = own_endpoint(g, direction, e), target_endpoint(g, direction, e)
            if mine and other:
                cmds += pp.connect_cmds(g, mine, other) if direction == "in" else pp.connect_cmds(g, other, mine)
    return cmds


# What Settings → Network shows: the saved streams and what's live. The Tui refreshes it.
View = namedtuple("View", "config units address host version rules graph", defaults=(None,))
