"""Patch tab monitors and app stream names (Skrappjaw, 2026-10-24, OBS):
- a monitor something records from is always listed, even with m off, under its own Monitors heading
- an app stream carries its own media.name when it says something (OBS · Desktop Audio), not when it's
  generic (Brave's "Playback", Discord's "recStream")."""
import json
from pathlib import Path

import pwcore as pc
import pwinventory as pi
import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
DUMP = json.loads((FIX / "pwdump_patch.json").read_text())


# --- names ------------------------------------------------------------------------

def stream(app, media, binary=None, cls="Stream/Input/Audio"):
    return {"media.class": cls, "application.name": app, "node.name": app, "media.name": media,
            "application.process.binary": binary or app.lower()}


def test_obs_streams_carry_their_media_name():
    assert pi.display_name(stream("OBS", "Desktop Audio", "obs")) == "OBS · Desktop Audio"
    assert pi.display_name(stream("OBS", "Mic/Aux 2", "obs")) == "OBS · Mic/Aux 2"


def test_generic_media_names_are_left_off():
    assert pi.display_name(stream("Brave", "Playback", "brave", "Stream/Output/Audio")) == "Brave"
    assert pi.display_name(stream("WEBRTC VoiceEngine", "recStream", "Discord")) == "Discord"
    assert pi.display_name(stream("WEBRTC VoiceEngine", "playStream", "Discord", "Stream/Output/Audio")) == "Discord"
    for generic in ("Capture", "Record", "Audio Stream", "ALSA Playback", "OBS", ""):
        assert pi.display_name(stream("OBS", generic, "obs")) == "OBS", generic


def test_devices_never_get_a_media_name():
    props = {"media.class": "Audio/Sink", "node.description": "GCS-8 CH USB", "media.name": "Something"}
    assert pi.display_name(props) == "GCS-8 CH USB"


# --- Patch monitors ---------------------------------------------------------------

def with_monitor_link(dump=DUMP):
    """The fixture plus one link: a GCS-8 output's monitor into Discord's recording stream."""
    g = pp.graph(dump)
    mon = next(s for s in g.sources if s.monitor and "GCS-8" in s.label)
    dst = next(d for d in g.dests if d.label == "Discord")
    a, b = mon.ports[0], dst.ports[0]
    link = {"id": 99999, "type": "PipeWire:Interface:Link",
            "info": {"output-node-id": a.node, "output-port-id": a.id, "input-node-id": b.node,
                     "input-port-id": b.id, "state": "active"}}
    return dump + [link], mon.label


def test_linked_monitor_shows_with_m_off():
    dump, label = with_monitor_link()
    g = pp.graph(dump)
    labels = [s.label for s in pc.patch_sources(g, pc.UIState(tab="patch"))]
    assert label in labels
    assert sum(1 for s in pc.patch_sources(g, pc.UIState(tab="patch")) if s.monitor) == 1  # only the linked one


def test_m_still_shows_every_monitor():
    g = pp.graph(with_monitor_link()[0])
    srcs = pc.patch_sources(g, pc.UIState(tab="patch", patch_monitors=True))
    assert sum(1 for s in srcs if s.monitor) > 1


def test_unlinked_monitors_stay_hidden_with_m_off():
    g = pp.graph(DUMP)
    assert not [s for s in pc.patch_sources(g, pc.UIState(tab="patch")) if s.monitor and not g.targets(s)]


def render(dump, width=140, height=60, **st):
    return pc.render_screen(pc.UIState(tab="patch", **st), {}, None, [], width, height, patch=pp.graph(dump))[0]


def test_column_view_has_a_monitors_heading_above_the_monitors():
    dump, label = with_monitor_link()
    left = [t.split("│")[0].rstrip() for t, _ in render(dump)]
    head = next(i for i, t in enumerate(left) if t.strip() == "Monitors")
    mon = next(i for i, t in enumerate(left) if label in t)
    assert head < mon
    assert all("(monitor)" not in t for t in left[:head])
    assert left.index(next(t for t in left if t.strip().startswith("Sources"))) < head


def test_monitors_heading_is_a_title_and_not_clickable():
    dump, _ = with_monitor_link()
    lines, hits = pc.render_screen(pc.UIState(tab="patch"), {}, None, [], 140, 60, patch=pp.graph(dump))
    i = next(n for n, (t, _) in enumerate(lines) if t.split("│")[0].strip() == "Monitors")
    assert ("title" in str(lines[i][1]))
    assert not [h for h in hits if h[0] == i and h[3][0] == "patch_src"]


def test_no_monitors_heading_without_monitors():
    left = [t.split("│")[0].strip() for t, _ in render(DUMP)]
    assert "Monitors" not in left


def test_grid_view_has_the_heading_too():
    dump, label = with_monitor_link()
    text = [t for t, _ in render(dump, patch_view="grid")]
    head = next(i for i, t in enumerate(text) if t.strip() == "Monitors")
    assert head < next(i for i, t in enumerate(text) if label in t)


def test_cursor_still_walks_every_source():
    dump, label = with_monitor_link()
    g = pp.graph(dump)
    srcs = pc.patch_sources(g, pc.UIState(tab="patch"))
    st = pc.UIState(tab="patch", patch_src=len(srcs) - 1)
    lines = render(dump, patch_src=len(srcs) - 1)
    assert any(t.startswith("> ") and label in t for t, _ in lines)
