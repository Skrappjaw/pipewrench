"""Which sample rates each sound card supports, from the kernel, and the rate it runs at now.

PipeWire only reports a node's current format, so the hardware's range comes from
/proc/asound/cardN/stream* instead. Only USB audio (snd-usb-audio) writes those files. Onboard and HDMI
(Intel HDA) cards list theirs in /proc/asound/cardN/codec#0 (punch list, 2026-10-26). Cards with neither
are "not reported" and get the generic warning. Background: Skrappjaw found, 2026-10-25, that forcing
192000 ran the graph at 192k while the UMC404HD stayed at 48000 (allowed-rates was [48000]).
"""
import re
from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc/asound")
GENERIC = "Make sure your device(s) support the rate selected."


@dataclass(frozen=True)
class Stream:
    """One card's stream files: the rates every direction accepts, and the rate it is running at."""
    rates: frozenset = frozenset()
    ranges: tuple = ()        # (lo, hi) continuous ranges every direction accepts
    running: int = None       # "Momentary freq" of a running direction, if any
    dirs: tuple = ()          # (("out"|"in", rates, ranges), ...): each direction's own list, for display only

    def supports(self, rate):
        return rate in self.rates or any(lo <= rate <= hi for lo, hi in self.ranges)


@dataclass(frozen=True)
class CardRates:
    label: str
    stream: Stream = None     # None: the kernel doesn't report this card's rates (or the card is off)
    status: str = None        # off | running | idle | suspended (Settings → Audio → Devices, Skrappjaw 2026-10-26)
    live: int = None          # the rate a running device of the card is at, from PipeWire
    nodes: tuple = ()         # ((node name, label), ...): its devices, so a driver shows by label

    @property
    def known(self):
        return self.stream is not None

    @property
    def running(self):
        return self.stream.running if self.stream else None

    def supports(self, rate):
        """True or False from the kernel's list; None when the card doesn't report one."""
        return self.stream.supports(rate) if self.stream else None


def parse_stream(text):
    """Rates from a stream file. A rate counts only if every direction (Playback, Capture) lists it."""
    dirs, current, running = {}, None, None
    for line in text.splitlines():
        if line in ("Playback:", "Capture:"):
            current = dirs.setdefault(line, (set(), []))
        elif current is not None and line.strip().startswith("Rates:"):
            body = line.split(":", 1)[1]
            span = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*\(continuous\)", body)
            if span:
                current[1].append((int(span[1]), int(span[2])))
            else:
                current[0].update(int(x) for x in re.findall(r"\d+", body))
        elif running is None and "Momentary freq" in line:
            m = re.search(r"=\s*(\d+)\s*Hz", line)
            running = int(m[1]) if m else None
    if not dirs:
        return Stream(running=running)
    per_dir = list(dirs.values())

    def in_dir(rate, d):
        return rate in d[0] or any(lo <= rate <= hi for lo, hi in d[1])

    rates = frozenset(r for d in per_dir for r in d[0] if all(in_dir(r, o) for o in per_dir))
    ranges = tuple(per_dir[0][1]) if all(d[1] for d in per_dir) else ()  # ranges: kept when every direction has one
    named = tuple(("out" if k == "Playback:" else "in", frozenset(v[0]), tuple(v[1])) for k, v in dirs.items())
    return Stream(rates=rates, ranges=ranges, running=running, dirs=named)


def parse_codec(text):
    """Rates from an HDA codec#0 file, or None if it lists none. Each Audio Output node is playback and each Audio
    Input node capture; a node without its own rates line uses the codec's "Default PCM" ones. A rate counts only
    if every node of every direction lists it (punch list: "only rates every part of the codec lists")."""
    default, nodes, kind = None, {"out": [], "in": []}, None
    for line in text.splitlines():
        if line.startswith("Node "):
            kind = "out" if "[Audio Output]" in line else "in" if "[Audio Input]" in line else None
            if kind:
                nodes[kind].append(None)
            continue
        m = re.match(r"\s+rates \[0x[0-9a-f]+\]:(.*)", line)
        if not m:
            continue
        rates = frozenset(int(x) for x in m[1].split())
        if kind is None and default is None and not any(nodes.values()):
            default = rates  # the function group's Default PCM, before the first node
        elif kind and nodes[kind] and nodes[kind][-1] is None:
            nodes[kind][-1] = rates
    per_dir = {d: [r if r is not None else default for r in ns] for d, ns in nodes.items() if ns}
    per_dir = {d: ns for d, ns in per_dir.items() if all(r is not None for r in ns)}
    if not per_dir:
        return None
    dirs = tuple((d, frozenset.intersection(*ns), ()) for d, ns in per_dir.items())
    return Stream(rates=frozenset.intersection(*(r for _, r, _ in dirs)), dirs=dirs)


def _codec(root, alsa_card):
    try:
        return parse_codec(Path(root, f"card{alsa_card}", "codec#0").read_text())
    except OSError:
        return None


def _status(card):
    states = {r.state for r in card.rows}
    return next((s for s in ("running", "idle", "suspended") if s in states), "suspended" if card.rows else "idle")


def read(cards, root=PROC):
    """CardRates for every card, in the order given, each with its status ("off" for profile off: no rates)."""
    out = []
    for c in cards:
        if c.active == "off":
            out.append(CardRates(c.label, None, status="off"))
            continue
        live = max((r.rate for r in c.rows if r.state == "running" and r.rate), default=None)
        files = sorted(Path(root, f"card{c.alsa_card}").glob("stream*")) if c.alsa_card is not None else []
        texts = []
        for f in files:
            try:
                texts.append(f.read_text())
            except OSError:
                pass
        stream = parse_stream("\n".join(texts)) if texts else _codec(root, c.alsa_card) if c.alsa_card is not None else None
        out.append(CardRates(c.label, stream, _status(c), live, tuple((r.name, r.label) for r in c.rows)))
    return out


def khz(rate):
    return f"{rate / 1000:g}k"


def _list(rates, ranges):
    if rates:
        return " ".join(f"{r / 1000:g}" for r in sorted(rates)) + "k"
    return " ".join(f"{lo / 1000:g}–{hi / 1000:g}k" for lo, hi in ranges)


def describe(stream):
    """'44.1 48 88.2 96 176.4 192k' or '8–96k'; with no rate common to both ways, each way's own list
    ('out 32k · in 16k': the DualShock 4, found on Skrappjaw's machine 2026-10-26)."""
    common = _list(stream.rates, stream.ranges)
    if common:
        return common
    return " · ".join(f"{d} {_list(r, g)}" for d, r, g in stream.dirs if _list(r, g)) or "none listed"


def warnings(rate, allowed, default, devices):
    """Warnings for forcing `rate`. devices is read()'s list, or None when rates weren't read."""
    if not rate:
        return []
    out = []
    devices = None if devices is None else [d for d in devices if d.status != "off"]  # an off card runs nothing
    for d in devices or []:
        if d.supports(rate) is False:
            out.append(f"{d.label} can't run at {khz(rate)} (it supports {describe(d.stream)})")
    if isinstance(allowed, list) and allowed and rate not in allowed:
        stay = f" and PipeWire resamples" if default else ""
        out.append(f"{khz(rate)} isn't in allowed rates ({', '.join(khz(r) for r in allowed)}), "
                   f"so devices stay at {khz(default) if default else 'their rate'}{stay}")
    if devices is None or any(not d.known for d in devices):
        out.append(GENERIC)
    return out
