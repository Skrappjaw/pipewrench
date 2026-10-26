"""Which sample rates each sound card supports, from the kernel, and the rate it runs at now.

PipeWire only reports a node's current format, so the hardware's range comes from
/proc/asound/cardN/stream* instead. Only USB audio (snd-usb-audio) writes those files; other cards
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

    def supports(self, rate):
        return rate in self.rates or any(lo <= rate <= hi for lo, hi in self.ranges)


@dataclass(frozen=True)
class CardRates:
    label: str
    stream: Stream = None     # None: the kernel doesn't report this card's rates

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
    return Stream(rates=rates, ranges=ranges, running=running)


def read(cards, root=PROC):
    """CardRates for every card that's on (active profile not "off"), in the order given."""
    out = []
    for c in cards:
        if c.active == "off":
            continue
        files = sorted(Path(root, f"card{c.alsa_card}").glob("stream*")) if c.alsa_card is not None else []
        texts = []
        for f in files:
            try:
                texts.append(f.read_text())
            except OSError:
                pass
        out.append(CardRates(c.label, parse_stream("\n".join(texts)) if texts else None))
    return out


def khz(rate):
    return f"{rate / 1000:g}k"


def describe(stream):
    """'44.1 48 88.2 96 176.4 192k' or '8–96k'."""
    if stream.rates:
        nums = [f"{r / 1000:g}" for r in sorted(stream.rates)]
        return " ".join(nums) + "k"
    return " ".join(f"{lo / 1000:g}–{hi / 1000:g}k" for lo, hi in stream.ranges) or "none listed"


def warnings(rate, allowed, default, devices):
    """Warnings for forcing `rate`. devices is read()'s list, or None when rates weren't read."""
    if not rate:
        return []
    out = []
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
