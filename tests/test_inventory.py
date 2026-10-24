import json
from pathlib import Path

import pytest

import pwinventory as pi

FIX = Path(__file__).parent / "fixtures"
GCS = "alsa_{}.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_{}_GCS8_0_{}_{}__{}"
CH2_IN = GCS.format("input", "in", 6, 7, "source")
MAIN_AUX = GCS.format("output", "out", 2, 3, "sink")


def dump():
    return json.loads((FIX / "pwdump_inventory.json").read_text())


@pytest.fixture
def inv():
    return pi.inventory(dump())


def card(inv, label):
    return next(c for c in inv.cards if c.label == label)


# --- grouping ------------------------------------------------------------------

def test_cards_include_every_audio_device_even_when_off(inv):
    labels = [c.label for c in inv.cards]
    assert labels == sorted(labels, key=str.lower)
    assert {"GCS-8", "DualShock 4 [CUH-ZCT2x]", "Rocksmith Guitar Adapter", "HDA ATI HDMI",
            "HD Pro Webcam C920", "Built-in Audio"} == set(labels)
    assert card(inv, "HDA ATI HDMI").rows == [] and card(inv, "HDA ATI HDMI").active == "off"


def test_gcs8_card_has_17_rows_outputs_first_in_channel_order(inv):
    rows = card(inv, "GCS-8").rows
    assert len(rows) == 17
    assert [r.label for r in rows[:7]] == ["GCS-8 CH USB", "GCS-8 MAIN-AUX", "GCS-8 CH 1-AUX", "GCS-8 CH 2-AUX",
                                            "GCS-8 CH 3-AUX", "GCS-8 CH 4-AUX", "GCS-8 CH SOUND PAD"]
    assert [r.direction for r in rows] == ["out"] * 7 + ["in"] * 10
    assert rows[7].label == "GCS-8 MAIN" and rows[-1].label == "GCS-8 CH SOUND PAD"
    assert all(r.kind == "hardware" for r in rows)


def test_card_profiles_and_active(inv):
    c = card(inv, "GCS-8")
    assert [(p.index, p.name) for p in c.profiles] == [(0, "off"), (1, "HiFi"), (2, "Direct"), (3, "pro-audio")]
    assert c.active == "HiFi"
    assert c.name.startswith("alsa_card.usb-BOSS_GCS-8")


def test_virtual_rows_are_the_buses_and_streams_are_left_out(inv):
    assert [r.name for r in inv.virtual] == ["MUSIC", "SYSTEM", "VOIP", "VOIP_MIC"]
    assert all(r.kind == "virtual" and r.direction == "duplex" for r in inv.virtual)
    every = [r.name for c in inv.cards for r in c.rows] + [r.name for r in inv.virtual]
    assert not any("webrtc" in n.lower() for n in every)


# --- row details ---------------------------------------------------------------

def test_row_state_format_volume(inv):
    ch2 = inv.find(CH2_IN)
    assert ch2.state == "running" and ch2.channels == 2 and ch2.rate == 48000 and ch2.format == "F32P"
    assert ch2.volume == pytest.approx(1.0) and ch2.mute is False
    aux = inv.find(MAIN_AUX)
    assert aux.volume == pytest.approx(0.40, abs=0.01)  # wpctl's cubic scale: 0.064 linear -> 0.40


def test_suspended_row_falls_back_to_props_for_channels(inv):
    r = inv.find("SYSTEM")
    assert r.state == "suspended" and r.channels in (2, None) and r.rate is None


def test_defaults_from_metadata(inv):
    assert inv.defaults == {"sink": MAIN_AUX, "source": CH2_IN}
    assert inv.find(MAIN_AUX).default and inv.find(CH2_IN).default
    assert sum(r.default for r in inv.rows()) == 2


def test_find_by_id_name_or_unique_label(inv):
    assert inv.find(96).name == CH2_IN
    assert inv.find("96").name == CH2_IN
    assert inv.find(CH2_IN).id == 96
    assert inv.find("gcs-8 ch 2").name == CH2_IN          # exact label, any case
    assert inv.find("nope") is None


def test_find_ambiguous_label_raises(inv):
    with pytest.raises(LookupError, match="matches"):
        inv.find("GCS-8 CH USB")  # an output and an input share it


def test_find_card_by_id_name_or_label(inv):
    assert inv.find_card("GCS-8").id == 95
    assert inv.find_card(95).label == "GCS-8"
    assert inv.find_card("alsa_card.usb-BOSS_GCS-8-01").id == 95
    assert inv.find_card("nope") is None


# --- monitors and hidden -------------------------------------------------------

def test_monitor_rows_one_per_sink(inv):
    mons = pi.monitor_rows(card(inv, "GCS-8").rows)
    assert len(mons) == 7
    assert mons[0].label == "GCS-8 CH USB (monitor)" and mons[0].direction == "in" and mons[0].kind == "monitor"
    assert mons[0].name.endswith("__sink.monitor")


def test_view_hides_and_adds_monitors(inv):
    hidden = {CH2_IN, "VOIP"}
    names = [r.name for _, r in pi.view(inv, hidden=hidden) if r]
    assert CH2_IN not in names and "VOIP" not in names
    assert not any(n.endswith(".monitor") for n in names)
    names2 = [r.name for _, r in pi.view(inv, hidden=hidden, show_hidden=True, monitors=True) if r]
    assert CH2_IN in names2 and any(n.endswith(".monitor") for n in names2)


def test_view_headings_carry_card_and_profile(inv):
    headings = [h for h, r in pi.view(inv) if r is None]
    assert "GCS-8 · profile HiFi" in headings
    assert not any("profile off" in h for h in headings)


def test_view_groups_off_cards_at_the_bottom(inv):
    headings = [h for h, r in pi.view(inv) if r is None]
    assert headings[headings.index("Virtual"):] == ["Virtual", "Off", "Built-in Audio · off",
                                                    "HD Pro Webcam C920 · off", "HDA ATI HDMI · off"]
    off = [c.name for c in inv.cards if c.active == "off"]
    headings = [h for h, r in pi.view(inv, hidden=off) if r is None]
    assert headings[-1] == "Virtual"  # no Off heading with nothing under it


def test_hidden_card_hides_its_heading_and_rows(inv):
    gcs = card(inv, "GCS-8")
    v = pi.view(inv, hidden={gcs.name})
    assert not any(h.startswith("GCS-8") for h, r in v if r is None)
    assert not any(r and r.card_id == 95 for _, r in v)


def test_inventory_tolerates_junk():
    junk = [{"type": "PipeWire:Interface:Node", "id": 1}, {"type": "PipeWire:Interface:Node", "id": 2, "info": None},
            {"type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"}, "metadata": []}]
    inv = pi.inventory(junk)
    assert inv.cards == [] and inv.virtual == [] and inv.defaults == {"sink": None, "source": None}


def test_read_dump_merges_update_documents():
    # pw-dump can print an update after the first array when the graph changes mid-read
    first = [{"id": 1, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "A"}}},
             {"id": 2, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "B"}}}]
    update = [{"id": 2, "info": None},
              {"id": 1, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "A2"}}},
              {"id": 3, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "C"}}}]
    text = json.dumps(first, indent=1) + "\n" + json.dumps(update, indent=1) + "\n"
    dump = pi.read_dump(lambda argv: (0, text))
    assert [(o["id"], o["info"]["props"]["node.name"]) for o in dump] == [(1, "A2"), (3, "C")]


def test_read_dump_single_document_unchanged():
    assert pi.read_dump(lambda argv: (0, json.dumps(dump()))) == dump()


def test_read_dump_garbage_raises():
    with pytest.raises(ValueError):
        pi.read_dump(lambda argv: (0, "[ not json"))


# --- connections ---------------------------------------------------------------------

CH_USB_OUT = "alsa_output.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_out_GCS8_0_0_1__sink"


def links_dump():
    return json.loads((FIX / "pwdump_links.json").read_text())


def _node(oid, cls, **props):
    return {"id": oid, "type": "PipeWire:Interface:Node", "info": {"state": "running",
            "props": {"media.class": cls, **props}, "params": {}}}


def _link(lid, out_id, in_id, state="active"):
    return {"id": lid, "type": "PipeWire:Interface:Link",
            "info": {"output-node-id": out_id, "input-node-id": in_id, "state": state}}


def test_live_links_name_apps_on_each_side():
    inv = pi.inventory(links_dump())
    assert inv.find(CH_USB_OUT).inputs == ("Brave",)          # Brave plays into CH USB
    assert inv.find(CH2_IN).outputs == ("cinnamon",)           # the sound applet meters CH 2
    assert inv.find(CH2_IN).inputs == () and inv.find(CH_USB_OUT).outputs == ()


def test_links_dedupe_per_channel_and_sort():
    d = links_dump()
    sink = pi.inventory(d).find(CH_USB_OUT).id
    d += [_node(800, "Stream/Output/Audio", **{"application.name": "Discord", "node.name": "discord"}),
          _link(801, 800, sink), _link(802, 800, sink)]
    assert pi.inventory(d).find(CH_USB_OUT).inputs == ("Brave", "Discord")


def test_monitor_readers_and_device_to_device_links():
    d = links_dump()
    inv0 = pi.inventory(d)
    sink, mic = inv0.find(CH_USB_OUT).id, inv0.find(CH2_IN).id
    d += [_node(900, "Audio/Duplex", **{"node.name": "OBS_BUS", "node.description": "OBS bus",
                                        "factory.name": "support.null-audio-sink"}),
          _node(901, "Stream/Input/Audio", **{"application.name": "OBS", "node.name": "obs"}),
          _link(902, sink, 901),   # OBS records CH USB's monitor
          _link(903, mic, 900),    # qpwgraph patch: CH 2 -> OBS bus
          _link(904, 900, 901)]    # OBS records the bus
    inv = pi.inventory(d)
    assert inv.find(CH_USB_OUT).outputs == ("OBS",)
    assert inv.find(CH2_IN).outputs == ("cinnamon", "OBS bus")  # case-insensitive order
    assert inv.find("OBS_BUS").inputs == ("GCS-8 CH 2",) and inv.find("OBS_BUS").outputs == ("OBS",)


def test_error_links_are_ignored():
    d = links_dump()
    sink = pi.inventory(d).find(CH_USB_OUT).id
    d += [_node(800, "Stream/Output/Audio", **{"application.name": "Ghost"}), _link(801, 800, sink, state="error")]
    assert pi.inventory(d).find(CH_USB_OUT).inputs == ("Brave",)


def test_connections_text_per_direction():
    d = links_dump()
    inv0 = pi.inventory(d)
    sink = inv0.find(CH_USB_OUT).id
    d += [_node(901, "Stream/Input/Audio", **{"application.name": "OBS"}), _link(902, sink, 901)]
    inv = pi.inventory(d)
    assert pi.connections(inv.find(CH_USB_OUT)) == "← Brave  → OBS (monitor)"
    assert pi.connections(inv.find(CH2_IN)) == "→ cinnamon"
    assert pi.connections(inv.find("alsa_output.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_out_GCS8_0_2_3__sink")) == ""


def test_monitor_row_shows_its_readers_only():
    d = links_dump()
    sink = pi.inventory(d).find(CH_USB_OUT).id
    d += [_node(901, "Stream/Input/Audio", **{"application.name": "OBS"}), _link(902, sink, 901)]
    mon = pi.monitor_rows([pi.inventory(d).find(CH_USB_OUT)])[0]
    assert mon.inputs == () and pi.connections(mon) == "→ OBS"
