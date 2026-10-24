"""Pure logic for pwctl Devices: find the GCS-8, model its named stereo pairs and buses, and
generate the PipeWire drop-in that makes them. No curses and no subprocess calls here; callers
pass a runner. Plan: docs/devices-plan.md.
"""
import difflib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pwcore

DUMP_CMD = ["pw-dump"]
HW_MATCH = "usb-BOSS_GCS-8"
HW_OUT = "alsa_output.usb-BOSS_GCS-8-01.pro-output-0"
HW_IN = "alsa_input.usb-BOSS_GCS-8-01.pro-input-0"
HW_CHANNELS = {"out": 14, "in": 20}
NODE_PREFIX = "pwctl.gcs8"
RATES = [r for r in pwcore.setting("clock.force-rate").choices if r]


class DevicesError(Exception):
    pass


# --- discovery ---------------------------------------------------------------

@dataclass(frozen=True)
class HwNode:
    name: str
    channels: int
    positions: list


def read_dump(run):
    rc, out = run(DUMP_CMD)
    if rc != 0:
        raise DevicesError(out.strip() or f"pw-dump exited {rc}")
    return json.loads(out)


def _props(obj):
    return ((obj or {}).get("info") or {}).get("props") or {}


def nodes(dump):
    return [_props(o) for o in dump if o.get("type") == "PipeWire:Interface:Node" and _props(o)]


def find_hw(dump, match=HW_MATCH):
    """The device's pro-audio output and input nodes, or None for each one not present."""
    found = {"out": None, "in": None}
    for p in nodes(dump):
        name = p.get("node.name", "")
        if match not in name:
            continue
        for d, prefix, cls in (("out", "alsa_output.", "Audio/Sink"), ("in", "alsa_input.", "Audio/Source")):
            if name.startswith(prefix) and ".pro-" in name and p.get("media.class") == cls:
                pos = [s.strip() for s in str(p.get("audio.position", "")).split(",") if s.strip()]
                found[d] = HwNode(name, int(p.get("audio.channels", len(pos))), pos)
    return found


# --- model -------------------------------------------------------------------

# GCS-8 Windows driver order (RDIF1240.INF), as (id, driver name, Skrappjaw's label).
# AUX index is twice the list position. Table: docs/devices-plan.md "GCS-8 channel map".
_DRIVER_PAIRS = {
    "in": [("main", "MAIN", ""), ("monitor", "MONITOR", ""), ("ch1", "CH 1", "Guitar"),
           ("ch2", "CH 2", "Mic"), ("ch3", "CH 3", ""), ("ch4", "CH 4", ""),
           ("chusb", "CH USB", ""), ("chmobile", "CH MOBILE", "Phone"),
           ("chbluetooth", "CH BLUETOOTH", ""), ("chsoundpad", "CH SOUND PAD", "")],
    "out": [("chusb", "CH USB", ""), ("mainaux", "MAIN-AUX", ""), ("ch1aux", "CH 1-AUX", ""),
            ("ch2aux", "CH 2-AUX", ""), ("ch3aux", "CH 3-AUX", "Music"), ("ch4aux", "CH 4-AUX", "Voip"),
            ("chsoundpad", "CH SOUND PAD", "")],
}
DEFAULT_BUSES = [{"name": "SYSTEM", "label": "SYSTEM"}, {"name": "GAME", "label": "GAME"}]

_ID = re.compile(r"[a-z0-9]{1,32}")
_BUS = re.compile(r"[A-Za-z0-9_-]{1,32}")


def default_model():
    pairs = []
    for d, table in _DRIVER_PAIRS.items():
        for i, (pid, name, label) in enumerate(table):
            pairs.append({"id": pid, "dir": d, "aux": 2 * i,
                          "label": f"GCS-8 {name}" + (f" ({label})" if label else ""), "enabled": True})
    return {"device": {"out": HW_OUT, "in": HW_IN}, "clock_rate": 48000,
            "pairs": pairs, "buses": [dict(b) for b in DEFAULT_BUSES]}


def pair_node_name(p):
    """Fixed per pair; never derived from the label, so app choices survive a relabel."""
    return f"{NODE_PREFIX}.{p['dir']}.{p['id']}"


def _check_label(label, what):
    if not isinstance(label, str) or not label.strip() or any(c in label for c in '"\\\n\r'):
        raise ValueError(f"{what}: label must be non-empty text without quotes, backslashes or newlines")


def validate_model(m, channels=None):
    channels = channels or HW_CHANNELS
    rate = m.get("clock_rate")
    if rate is not None and rate not in RATES:
        raise ValueError(f"clock rate {rate!r} is not one of {RATES}")
    seen_ids, seen_aux, seen_labels = set(), set(), set()
    for p in m.get("pairs", []):
        what = f"pair {p.get('dir')}/{p.get('id')}"
        if p.get("dir") not in ("in", "out"):
            raise ValueError(f"{what}: dir must be 'in' or 'out'")
        if not isinstance(p.get("id"), str) or not _ID.fullmatch(p["id"]):
            raise ValueError(f"{what}: id must be 1-32 lowercase letters or digits")
        aux = p.get("aux")
        if isinstance(aux, bool) or not isinstance(aux, int) or aux % 2:
            raise ValueError(f"{what}: aux must be an even whole number")
        if not 0 <= aux <= channels[p["dir"]] - 2:
            raise ValueError(f"{what}: aux {aux} is out of range for {channels[p['dir']]} channels")
        _check_label(p.get("label"), what)
        if not isinstance(p.get("enabled"), bool):
            raise ValueError(f"{what}: enabled must be true or false")
        if (p["dir"], p["id"]) in seen_ids:
            raise ValueError(f"{what}: duplicate id")
        if (p["dir"], aux) in seen_aux:
            raise ValueError(f"{what}: AUX{aux}/AUX{aux + 1} overlaps another pair")
        if (p["dir"], p["label"]) in seen_labels:
            raise ValueError(f"{what}: duplicate label {p['label']!r}")
        seen_ids.add((p["dir"], p["id"]))
        seen_aux.add((p["dir"], aux))
        seen_labels.add((p["dir"], p["label"]))
    seen_buses = set()
    for b in m.get("buses", []):
        name = b.get("name")
        if not isinstance(name, str) or not _BUS.fullmatch(name):
            raise ValueError(f"bus name {name!r} must be 1-32 letters, digits, - or _")
        if name in seen_buses:
            raise ValueError(f"bus {name}: duplicate name")
        _check_label(b.get("label"), f"bus {name}")
        seen_buses.add(name)


def devices_path():
    if os.environ.get("PWCTL_DEVICES"):
        return Path(os.environ["PWCTL_DEVICES"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "pwctl" / "devices.json"


def load_model(path):
    path = Path(path)
    if not path.exists():
        return default_model()
    m = json.loads(path.read_text())
    validate_model(m)
    return m


def save_model(path, m):
    validate_model(m)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(m, indent=2) + "\n")
    os.replace(tmp, path)


# --- drop-in generator (SPA-JSON) --------------------------------------------

HEADER = ("# Generated by pwctl from devices.json. Do not edit by hand:\n"
          "# change devices with `pwctl devices`, then apply.\n")
STEREO = "[ FL FR ]"


def _block(lines, indent):
    return "".join(" " * indent + line + "\n" for line in lines)


def render_pair(p, m):
    """One loopback per pair: a stereo app-facing node hard-wired to two AUX channels."""
    name = pair_node_name(p)
    app = [f'node.name = "{name}"',
           f'media.class = "{"Audio/Sink" if p["dir"] == "out" else "Audio/Source"}"',
           f"audio.position = {STEREO}"]
    hw = [f'node.name = "{name}.hw"',
          f'target.object = "{m["device"][p["dir"]]}"',
          f'audio.position = [ AUX{p["aux"]} AUX{p["aux"] + 1} ]',
          "stream.dont-remix = true",
          "node.passive = true"]
    # Loopback capture feeds playback: an output pair captures from apps, an input pair from hardware.
    capture, playback = (app, hw) if p["dir"] == "out" else (hw, app)
    return ("    {   name = libpipewire-module-loopback\n"
            "        args = {\n"
            f'            node.description = "{p["label"]}"\n'
            "            capture.props = {\n" + _block(capture, 16) + "            }\n"
            "            playback.props = {\n" + _block(playback, 16) + "            }\n"
            "        }\n"
            "    }\n")


def render_bus(b):
    args = ["factory.name = support.null-audio-sink",
            f'node.name = "{b["name"]}"',
            f'node.description = "{b["label"]}"',
            'media.class = "Audio/Duplex"',
            f"audio.position = {STEREO}",
            "object.linger = true",
            "monitor.channel-volumes = true"]
    return "    {   factory = adapter\n        args = {\n" + _block(args, 12) + "        }\n    }\n"


def render_dropin(m):
    validate_model(m)
    parts = [HEADER]
    if m.get("clock_rate"):
        parts.append(f"context.properties = {{\n    default.clock.rate = {m['clock_rate']}\n}}\n")
    pairs = [render_pair(p, m) for p in m["pairs"] if p["enabled"]]
    if pairs:
        parts.append("context.modules = [\n" + "".join(pairs) + "]\n")
    if m.get("buses"):
        parts.append("context.objects = [\n" + "".join(render_bus(b) for b in m["buses"]) + "]\n")
    return "\n".join(parts)


# --- apply / rollback --------------------------------------------------------

RESTART_CMD = ["systemctl", "--user", "restart", "pipewire", "pipewire-pulse", "wireplumber"]


def dropin_path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "pipewire" / "pipewire.conf.d" / "50-pwctl-devices.conf"


def _backup(path):
    return path.with_name(path.name + ".bak")


def _check_ours(path):
    if path.exists() and not path.read_text().startswith(HEADER.splitlines()[0]):
        raise DevicesError(f"{path} was not generated by pwctl; move it aside first")


def diff_dropin(path, text):
    path = Path(path)
    old = path.read_text() if path.exists() else ""
    return "".join(difflib.unified_diff(old.splitlines(True), text.splitlines(True),
                                        f"{path} (current)", f"{path} (new)"))


def write_dropin(path, text):
    """Atomic write, keeping the previous file as .bak. Returns False when nothing changed."""
    path = Path(path)
    _check_ours(path)
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, _backup(path))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return True


def rollback_dropin(path):
    path = Path(path)
    _check_ours(path)
    if _backup(path).exists():
        os.replace(_backup(path), path)
        return f"restored the previous {path.name}"
    if path.exists():
        path.unlink()
        return f"removed {path.name}; PipeWire is back to its stock config"
    return "nothing to roll back"
