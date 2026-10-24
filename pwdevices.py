"""Pure logic for pwctl Devices: find the GCS-8, model its named stereo pairs and buses, and
generate the PipeWire drop-in that makes them. No curses and no subprocess calls here; callers
pass a runner. Plan: docs/devices-plan.md.
"""
import json
from dataclasses import dataclass

DUMP_CMD = ["pw-dump"]
HW_MATCH = "usb-BOSS_GCS-8"


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
