#!/usr/bin/env python3
"""Draw the user guide's screenshots (docs/guide.md) from the test fixtures.

Each picture is the frame pwrench paints: pwcore.render_screen gives the same lines and colour roles the
curses UI draws, and this script paints them as PNG in the dark theme, one character per cell in DejaVu
Sans Mono (a few shots show the other themes). Fixtures only, so nothing from a live machine ends up in the docs, and the pictures can be
redrawn whenever the UI changes:

    python3 tools/screenshots.py            # writes docs/guide/*.png

Needs Pillow (python3-pil); pwrench itself doesn't.
"""
import copy
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pwconfig as pcfg  # noqa: E402
import pwcore as pc  # noqa: E402
import pwinventory as pi  # noqa: E402
import pwmedia as pm  # noqa: E402
import pwpatch as pp  # noqa: E402
import pwrealtime as prt  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_patch import with_reaper  # noqa: E402  (REAPER as pipewire-jack shows it)

FIX = ROOT / "tests" / "fixtures"
OUT = ROOT / "docs" / "guide"
W, H = 100, 30

# --- the dark theme, as a typical dark terminal shows its eight colours --------------------------
PALETTE = {"black": "#1d1f21", "red": "#e05252", "green": "#7cc243", "yellow": "#e5c07b", "blue": "#3e6fb5",
           "magenta": "#c678dd", "cyan": "#4fb6c6", "white": "#d0d0d0", "brightwhite": "#ffffff"}
SCREEN_BG, SCREEN_FG = "#1d1f21", "#d0d0d0"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT, FONT_BOLD = (ImageFont.truetype(str(FONT_DIR / f), 15) for f in ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"))
CELL_W, CELL_H = round(FONT.getlength("M")), 19


def _screen(theme):
    """(fg, bg) of the window: the theme's own screen colours when it names hex, else the dark terminal's."""
    fg, bg, _ = pc.THEMES[theme]["screen"]
    return (fg if fg and fg.startswith("#") else SCREEN_FG), (bg if bg and bg.startswith("#") else SCREEN_BG)


def _role(name, theme="dark"):
    fg, bg, attrs = pc.THEMES[theme][name]
    screen_fg, screen_bg = _screen(theme)
    colour = lambda c: c if c.startswith("#") else PALETTE.get(c)  # noqa: E731
    fg, bg = colour(fg) if fg else screen_fg, colour(bg) if bg else None
    attrs = attrs.split()
    if "reverse" in attrs:
        fg, bg = bg or screen_bg, fg
    return fg, bg, "bold" in attrs, "dim" in attrs, "underline" in attrs


def _cells(text, style, width, theme="dark"):
    """Per-cell (fg, bg, bold, dim, underline), painting the base role over the text and then each span in
    order, as Tui._draw does."""
    plain = (_screen(theme)[0], None, False, False, False)
    base, spans = (style, []) if not isinstance(style, tuple) else style
    cells = [_role(base, theme) if n < len(text) else plain for n in range(width)]
    for c0, c1, role in spans:
        for n in range(max(0, c0), min(c1, len(text), width)):
            cells[n] = _role(role, theme)
    return cells


def _hex(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def _blend(fg, bg, k=0.55):
    return tuple(round(b + (f - b) * k) for f, b in zip(fg, bg))


def png(lines, title="pwrench", theme="dark"):
    """A terminal window around the frame, every character placed on its cell so box lines meet."""
    top, pad = 30, 10
    screen_bg = _screen(theme)[1]
    img = Image.new("RGB", (W * CELL_W + 2 * pad, len(lines) * CELL_H + top + pad), _hex(screen_bg))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, img.width, top - 6), fill=_hex("#2c2f33"))
    for k, c in enumerate(("#e05252", "#e5c07b", "#7cc243")):
        draw.ellipse((12 + 18 * k, 7, 22 + 18 * k, 17), fill=_hex(c))
    draw.text((img.width / 2, 12), title, fill=_hex("#9aa0a6"), font=FONT, anchor="mm")
    for y, (text, style) in enumerate(lines):
        text = text[:W]
        cells = _cells(text.rstrip(), style, W, theme)
        by = top + y * CELL_H
        for x, ch in enumerate(text.ljust(W)):
            fg, bg, bold, dim, under = cells[x]
            px = pad + x * CELL_W
            back = _hex(bg) if bg else _hex(screen_bg)
            if bg:
                draw.rectangle((px, by, px + CELL_W - 1, by + CELL_H - 1), fill=back)
            if ch.strip():
                colour = _hex(fg)
                if dim:
                    colour = _blend(colour, back)
                if ch in "█▀▄░▐▌":  # block glyphs fill the whole cell, like a terminal draws them
                    _block(draw, ch, px, by, colour, back)
                elif ch in _BOX:  # box lines join their neighbours across cells
                    _box(draw, ch, px, by, colour)
                else:
                    draw.text((px, by + 2), ch, fill=colour, font=FONT_BOLD if bold else FONT)
                if under:
                    draw.line((px, by + CELL_H - 2, px + CELL_W - 1, by + CELL_H - 2), fill=colour)
    return img


# Box-drawing characters as the sides of the cell they reach: up, down, left, right.
_BOX = {"─": "lr", "│": "ud", "┌": "dr", "┐": "dl", "└": "ur", "┘": "ul", "├": "udr", "┤": "udl",
        "┬": "dlr", "┴": "ulr", "┼": "udlr"}


def _box(draw, ch, x, y, colour):
    """Each stroke a 2-pixel rectangle from the cell's centre to the side it reaches."""
    cx, cy = x + CELL_W // 2, y + CELL_H // 2
    rects = {"u": (cx, y, cx + 1, cy + 1), "d": (cx, cy, cx + 1, y + CELL_H - 1),
             "l": (x, cy, cx + 1, cy + 1), "r": (cx, cy, x + CELL_W - 1, cy + 1)}
    for side in _BOX[ch]:
        draw.rectangle(rects[side], fill=colour)


def _block(draw, ch, x, y, colour, back):
    w, h = CELL_W, CELL_H
    box = {"█": (0, 0, w, h), "▀": (0, 0, w, h // 2), "▄": (0, h // 2, w, h),
           "▐": (w // 2, 0, w, h), "▌": (0, 0, w // 2, h)}.get(ch)
    if box:
        draw.rectangle((x + box[0], y + box[1], x + box[2] - 1, y + box[3] - 1), fill=colour)
    else:  # ░: a light shade
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=_blend(colour, back, 0.3))


# --- fixture data, with OBS, Brave and REAPER added the way PipeWire reports them ----------------

def _clone_stream(d, template_id, new_id, props, port_base):
    node = copy.deepcopy(next(o for o in d if o.get("id") == template_id))
    node["id"] = new_id
    node["info"]["props"] = {**node["info"]["props"], **props, "object.id": new_id}
    ports = []
    for k, o in enumerate(o for o in d if o.get("type") == "PipeWire:Interface:Port"
                          and o["info"]["props"].get("node.id") == template_id):
        p = copy.deepcopy(o)
        p["id"] = port_base + k
        p["info"]["props"]["node.id"] = new_id
        ports.append(p)
    return [node] + ports


def _link(d, link_id, src_ports, dst_ports):
    for k, (a, b) in enumerate(zip(src_ports, dst_ports)):
        d.append({"id": link_id + k, "type": "PipeWire:Interface:Link",
                  "info": {"output-node-id": a.node, "output-port-id": a.id, "input-node-id": b.node,
                           "input-port-id": b.id, "state": "active"}})


def studio_dump():
    """The patch fixture plus OBS (Desktop Audio from CH USB's monitor, Mic/Aux from CH 2), Brave playing
    into CH USB, and REAPER over JACK."""
    d = json.loads((FIX / "pwdump_patch.json").read_text())
    rec = next(o["id"] for o in d if (o.get("info") or {}).get("props", {}).get("media.class") == "Stream/Input/Audio")
    play = next(o["id"] for o in d if (o.get("info") or {}).get("props", {}).get("media.class") == "Stream/Output/Audio")
    obs = {"application.name": "OBS", "application.process.binary": "obs", "node.name": "OBS"}
    d += _clone_stream(d, rec, 900, {**obs, "media.name": "Desktop Audio"}, 9000)
    d += _clone_stream(d, rec, 901, {**obs, "media.name": "Mic/Aux"}, 9010)
    d += _clone_stream(d, play, 902, {"application.name": "Brave", "application.process.binary": "brave",
                                      "node.name": "Brave", "media.name": "Playback"}, 9020)
    d = with_reaper(d)
    g = pp.graph(d)
    usb_mon = next(s for s in g.sources if s.monitor and s.label.startswith("GCS-8 CH USB"))
    ch2 = next(s for s in g.sources if s.label == "GCS-8 CH 2" and not s.monitor)
    usb_out = next(t for t in g.dests if t.label == "GCS-8 CH USB")
    _link(d, 9100, usb_mon.ports, next(t for t in g.dests if t.node == 900).ports)
    _link(d, 9110, ch2.ports, next(t for t in g.dests if t.node == 901).ports)
    _link(d, 9120, next(s for s in g.sources if s.node == 902).ports, usb_out.ports)
    _link(d, 9130, ch2.ports, next(t for t in g.dests if t.node == 700).ports[:2])  # CH 2 into REAPER In 1-2
    return d


def frame(st, height=H, **kw):
    lines, _ = pc.render_screen(st, CUR, kw.pop("block", None), kw.pop("presets", PRESETS), W, height, **kw)
    return lines


def prompt(lines, question):
    """A text prompt as Tui._prompt draws it: over the status line, in the alert colour."""
    return lines[:-1] + [(f" {question} ", "alert")]


CUR = pc.parse_settings((FIX / "settings.txt").read_text())
PRESETS = [{"name": "tracking", "label": "Tracking (low latency)", "values": {"clock.force-quantum": 128}},
           {"name": "mixing", "label": "Mixing", "values": {"clock.force-quantum": 1024}}]
DUMP = studio_dump()
INV = pi.inventory(DUMP)
STREAMS = pi.streams(DUMP)
CONFIG = pcfg.default_config()
BUS_INV = pi.inventory(json.loads((FIX / "pwdump_inventory.json").read_text()))  # has buses and Off cards
PLAYER = pm.Player("org.mpris.MediaPlayer2.brave", "Brave", "Playing", "Weightless", "Marconi Union",
                   position=131_000_000, length=480_000_000)
MEDIA = {"player": PLAYER, "position": 131_000_000}


def mixer(meters=True):
    m = pc.mixer_view(INV, CONFIG, STREAMS)
    if meters:
        rows = [(sec, r) for _, sec, r in pc.mixer_selectable(m)]
        lit = {"GCS-8 CH USB": (0.62, 0.71), "GCS-8 CH 2": (0.48, 0.55), "Brave": (0.6, 0.7),
               "OBS · Mic/Aux": (0.46, 0.52)}
        m["metered"] = {(sec, r.id): r.serial for sec, r in rows if r.label in lit}
        m["levels"] = {(sec, r.id): lit[r.label] for sec, r in rows if r.label in lit}
    return m


def mix_at(m, label, section="playback"):
    return next(n for n, (_, sec, r) in enumerate(pc.mixer_selectable(m)) if r.label == label and sec == section)


def devices(inv=BUS_INV):
    return pc.devices_view(inv, CONFIG)


def dev_at(d, kind, label):
    return next(n for n, (k, o) in enumerate(pc.dev_selectable(d, pc.UIState(tab="devices")))
                if k == kind and o.label == label)


def patch_at(g, st, source=None, dest=None):
    srcs = pc.patch_sources(g, st)
    if source:
        st.patch_src = next(n for n, s in enumerate(srcs) if s.label == source)
    if dest:
        st.patch_dst = next(n for n, t in enumerate(g.dests) if t.label == dest)
    return st


def realtime_status(ok):
    proc = prt.Proc(4242, "pipewire", 1800, 95 if ok else 0,
                    tuple(prt.Thread(4250 + k, 1 if ok else 0, 88 if ok else 0) for k in range(2)))
    pulse = prt.Proc(4243, "pipewire-pulse", 1800, 95 if ok else 0, (prt.Thread(4260, 1 if ok else 0, 83 if ok else 0),))
    return prt.Status("user", {"pipewire": 95}, True, (proc, pulse), 0 if ok else 12)


def shots():
    m = mixer()
    g = pp.graph(DUMP)
    top = pc.TopParser()
    for line in (FIX / "pwtop_batch.txt").read_text().splitlines():
        top.feed(line)
    block = top.flush()

    yield "mixer", frame(pc.UIState(tab="mixer", mix_cursor=mix_at(m, "GCS-8 CH USB")), mixer=m, media=MEDIA)
    yield "mixer-pipeboy", frame(pc.UIState(tab="mixer", theme="pipeboy", mix_cursor=mix_at(m, "GCS-8 CH USB")),
                                 mixer=m, media=MEDIA)
    yield "mixer-vaporware", frame(pc.UIState(tab="mixer", theme="vaporware", mix_cursor=mix_at(m, "GCS-8 CH USB")),
                                   mixer=m, media=MEDIA)
    yield "mixer-bar-off", frame(pc.UIState(tab="mixer", volume_bar=False, mix_cursor=mix_at(m, "GCS-8 CH USB")),
                                 mixer=m, media=MEDIA)
    st, _ = pc.handle_key(pc.UIState(tab="mixer", mix_cursor=mix_at(m, "GCS-8 CH 2", "recording")), "enter", CUR, [], mixer=m)
    yield "mixer-menu", frame(st, mixer=m, media=MEDIA)
    st.mix_menu = None
    yield "mixer-rename", prompt(frame(st, mixer=m, media=MEDIA), "new name for GCS-8 CH 2 (empty restores the default): Mic")

    d = devices()
    yield "devices", frame(pc.UIState(tab="devices"), devices=d, media=MEDIA)
    st = pc.focus_device(pc.UIState(tab="devices", dev_focus=("card", BUS_INV.find_card("GCS-8").id)), d)
    yield "devices-profile", frame(st, devices=d, media=MEDIA)
    st = pc.UIState(tab="devices", dev_cursor=dev_at(d, "row", "SYSTEM Audio/Duplex sink"))
    st, _ = pc.handle_key(st, "enter", CUR, [], devices=d)
    yield "devices-bus", frame(st, devices=d, media=MEDIA)
    add = next(n for n, (k, _) in enumerate(pc.dev_selectable(d, pc.UIState(tab="devices"))) if k == "addbus")
    yield "devices-add-bus", prompt(frame(pc.UIState(tab="devices", dev_cursor=add), devices=d, media=MEDIA),
                                    "bus name (letters, digits, - _; empty cancels): OBS")

    st = patch_at(g, pc.UIState(tab="patch"), source="GCS-8 CH 2")
    yield "patch", frame(st, patch=g, media=MEDIA)
    yield "patch-portal", frame(st, patch=g, media=MEDIA)
    st = patch_at(g, pc.UIState(tab="patch"), source="GCS-8 CH USB (monitor)")
    yield "patch-monitors", frame(st, patch=g, media=MEDIA)
    st = patch_at(g, pc.UIState(tab="patch", patch_view="grid"), source="GCS-8 CH 2", dest="REAPER")
    yield "patch-grid", frame(st, patch=g, media=MEDIA)
    st = patch_at(g, pc.UIState(tab="patch", patch_side="dst", patch_menu=True), source="GCS-8 CH 4", dest="REAPER")
    yield "patch-slots", frame(st, patch=g, media=MEDIA)
    st = patch_at(g, pc.UIState(tab="patch", patch_ports=True, patch_side="dst"), source="GCS-8 CH 2", dest="REAPER")
    yield "patch-ports", frame(st, patch=g, media=MEDIA)

    # Settings scrolls to the cursor; these are taken on a taller terminal (40 rows) to show more of it.
    yield "settings", frame(pc.UIState(tab="settings", cursor=1, pending={"clock.min-quantum": 64}), height=40,
                            media=MEDIA, realtime=realtime_status(False))
    yield "settings-realtime-ok", frame(pc.UIState(tab="settings", cursor=pc.BAR_ROW), height=40, media=MEDIA,
                                        realtime=realtime_status(True))
    yield "settings-presets", frame(pc.UIState(tab="settings", cursor=pc.PRESETS_ROW), height=40, media=MEDIA,
                                    realtime=realtime_status(True))
    yield "settings-keys", frame(pc.UIState(tab="settings", cursor=pc.KEYS_ROW + 1,
                                            keys={"devices.show_hidden": "z", "mixer.meters": None}),
                                 height=40, media=MEDIA, realtime=realtime_status(True))
    yield "about-pipecraft", frame(pc.UIState(tab="about", theme="pipecraft"), media=MEDIA,
                                   about={"pipewire": "1.0.5", "wireplumber": "0.4.17", "config": "~/.config/pipewrench"})
    yield "top", frame(pc.UIState(tab="top"), block=block, media=MEDIA)
    yield "about", frame(pc.UIState(tab="about"), media=MEDIA,
                         about={"pipewire": "1.0.5", "wireplumber": "0.4.17", "config": "~/.config/pipewrench"})


SHOT_THEMES = {"mixer-pipeboy": "pipeboy", "mixer-vaporware": "vaporware", "about-pipecraft": "pipecraft",
               "patch-portal": "pipeos-dark"}  # the rest are drawn in the dark theme


def main(out=OUT):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for name, lines in shots():
        png(lines, theme=SHOT_THEMES.get(name, "dark")).save(out / f"{name}.png", optimize=True)
        names.append(name)
    return names


if __name__ == "__main__":
    for n in main(*sys.argv[1:]):
        print(f"docs/guide/{n}.png")
