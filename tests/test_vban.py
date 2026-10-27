import json

import pytest

import pwvban as pv

# Headers captured from the Studio's PipeWire 1.0.5 vban-send, 2026-10-26 (notes/vban-plan.md).
AUDIO_44K_MONO_24 = b"VBAN" + bytes([0x10, 0xFF, 0x00, 0x02]) + b"probe44m".ljust(16, b"\0") + (0).to_bytes(4, "little")
MIDI = b"VBAN" + bytes([0x2E, 0x00, 0x00, 0x10]) + b"midi1".ljust(16, b"\0") + (1).to_bytes(4, "little")


@pytest.fixture
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def entry(**kw):
    return {**pv.new_entry("in", kw.pop("name", "Studio"), kw.pop("ip", "192.168.1.86")), **kw}


# --- headers --------------------------------------------------------------------

def test_header_audio_from_a_real_packet():
    h = pv.parse_header(AUDIO_44K_MONO_24 + b"\0" * 768)
    assert h == pv.Heard(stream="probe44m", media="audio", rate=44100, channels=1, format="S24LE")


def test_header_midi_from_a_real_packet():
    h = pv.parse_header(MIDI + b"\x90\x3c\x64")
    assert h.stream == "midi1" and h.media == "midi"


def test_header_rejects_other_traffic():
    assert pv.parse_header(b"hello") is None
    assert pv.parse_header(b"XBAN" + AUDIO_44K_MONO_24[4:]) is None
    text = b"VBAN" + bytes([0x40, 0, 0, 0x10]) + b"cmd".ljust(16, b"\0") + bytes(4)  # VBAN-TEXT: not a stream
    assert pv.parse_header(text) is None


def test_header_48k_stereo_16():
    h = pv.parse_header(b"VBAN" + bytes([3, 0x7F, 1, 1]) + b"Stream1".ljust(16, b"\0") + bytes(4))
    assert (h.rate, h.channels, h.format) == (48000, 2, "S16LE")


def test_heard_label():
    h = pv.Heard("Studio", "audio", 48000, 2, "S16LE")
    assert pv.heard_label(h, "192.168.1.86") == "Studio · 192.168.1.86 · 48 kHz · 2 ch"
    assert pv.heard_label(pv.Heard("nanoK", "midi"), "192.168.1.86") == "nanoK · 192.168.1.86 · MIDI"


# --- versions -------------------------------------------------------------------

def test_version_from_pipewire_version_output():
    out = "pipewire\nCompiled with libpipewire 1.0.5\nLinked with libpipewire 1.0.5\n"
    assert pv.parse_version(out) == (1, 0, 5)
    assert pv.parse_version("nonsense") is None


def test_rules_need_1_4_2():
    assert not pv.supports_rules((1, 0, 5))
    assert not pv.supports_rules(None)
    assert pv.supports_rules((1, 4, 2)) and pv.supports_rules((1, 6, 0))


# --- entries ------------------------------------------------------------------

def test_new_entry_defaults():
    e = pv.new_entry("out", "Voicemeeter PC", "192.168.1.20")
    assert e == {"id": "voicemeeter-pc", "name": "Voicemeeter PC", "ip": "192.168.1.20", "port": 6980,
                 "stream": "Voicemeeter PC", "media": "audio", "rate": 48000, "format": "S16LE", "channels": 2,
                 "target": None, "on": True, "latency": 40}


def test_add_entry_gives_unique_ids_and_ports_on_1_0():
    c = pv.default_config()
    c = pv.add_entry(c, "in", pv.new_entry("in", "Studio", "192.168.1.86"), rules=False)
    c = pv.add_entry(c, "in", pv.new_entry("in", "Studio", "192.168.1.87"), rules=False)
    assert [e["id"] for e in c["incoming"]] == ["studio", "studio-2"]
    assert [e["port"] for e in c["incoming"]] == [6980, 6981]  # 1.0.5: one stream per port


def test_add_entry_shares_the_port_with_rules():
    c = pv.default_config()
    c = pv.add_entry(c, "in", pv.new_entry("in", "A", "10.0.0.1"), rules=True)
    c = pv.add_entry(c, "in", pv.new_entry("in", "B", "10.0.0.2"), rules=True)
    assert [e["port"] for e in c["incoming"]] == [6980, 6980]


def test_outgoing_ports_stay_6980():
    c = pv.default_config()
    for n in ("A", "B"):
        c = pv.add_entry(c, "out", pv.new_entry("out", n, "10.0.0.1"), rules=False)
    assert [e["port"] for e in c["outgoing"]] == [6980, 6980]


def test_ports_skip_ones_in_use_after_a_remove():
    c = pv.default_config()
    for n in ("A", "B", "C"):
        c = pv.add_entry(c, "in", pv.new_entry("in", n, "10.0.0.1"), rules=False)
    c = pv.remove_entry(c, "in", "a")
    c = pv.add_entry(c, "in", pv.new_entry("in", "D", "10.0.0.1"), rules=False)
    assert sorted(e["port"] for e in c["incoming"]) == [6980, 6981, 6982]


@pytest.mark.parametrize("bad", ["", "999.1.1.1", "studio.local", "192.168.50", "::1"])
def test_bad_ip_refused(bad):
    with pytest.raises(ValueError):
        pv.new_entry("in", "Studio", bad)


@pytest.mark.parametrize("bad", ["", "  ", 'a"b', "x\\y", "a\nb"])
def test_bad_name_refused(bad):
    with pytest.raises(ValueError):
        pv.new_entry("in", bad, "10.0.0.1")


def test_stream_name_is_cut_to_16():
    assert pv.new_entry("out", "A very long machine name", "10.0.0.1")["stream"] == "A very long mach"


def test_update_and_remove():
    c = pv.add_entry(pv.default_config(), "in", entry(), rules=False)
    c = pv.update_entry(c, "in", "studio", target="alsa_output.x", on=False)
    assert pv.find(c, "in", "studio")["target"] == "alsa_output.x" and not pv.find(c, "in", "studio")["on"]
    assert pv.find(c, "in", "Studio")["id"] == "studio"  # by name too
    c = pv.remove_entry(c, "in", "studio")
    assert c["incoming"] == []
    with pytest.raises(LookupError):
        pv.remove_entry(c, "in", "studio")


def test_set_format_switches_media():
    c = pv.add_entry(pv.default_config(), "in", entry(), rules=False)
    c = pv.set_format(c, "in", "studio", "midi")
    assert pv.find(c, "in", "studio")["media"] == "midi"
    c = pv.set_format(c, "in", "studio", (44100, "S16LE", 1))
    e = pv.find(c, "in", "studio")
    assert (e["media"], e["rate"], e["format"], e["channels"]) == ("audio", 44100, "S16LE", 1)


def test_from_heard_fills_in_the_packet():
    e = pv.from_heard("in", pv.Heard("Studio mix", "audio", 44100, 1, "S24LE"), "192.168.1.86")
    assert (e["name"], e["stream"], e["ip"], e["rate"], e["channels"], e["format"]) == \
        ("Studio mix", "Studio mix", "192.168.1.86", 44100, 1, "S24LE")


def test_config_round_trip(xdg):
    c = pv.add_entry(pv.default_config(), "in", entry(), rules=False)
    pv.save_config(pv.config_path(), c)
    assert pv.load_config(pv.config_path()) == c
    assert pv.config_path() == xdg / "pipewrench" / "vban.json"
    assert pv.load_config(xdg / "missing.json") == pv.default_config()


def test_load_refuses_a_broken_file(xdg):
    p = xdg / "vban.json"
    p.write_text(json.dumps({"version": 1, "incoming": [{"id": "x"}], "outgoing": []}))
    with pytest.raises(ValueError):
        pv.load_config(p)


# --- generated files --------------------------------------------------------------

def test_instances_on_1_0_one_listener_per_incoming():
    c = pv.default_config()
    c = pv.add_entry(c, "in", entry(), rules=False)
    c = pv.add_entry(c, "in", entry(name="Desk", ip="192.168.1.20"), rules=False)
    c = pv.add_entry(c, "out", pv.new_entry("out", "Studio", "192.168.1.86"), rules=False)
    assert sorted(pv.instances(c, rules=False)) == ["in-desk", "in-studio", "out-studio"]


def test_instances_with_rules_share_one_listener_per_port():
    c = pv.default_config()
    c = pv.add_entry(c, "in", entry(), rules=True)
    c = pv.add_entry(c, "in", entry(name="Desk", ip="192.168.1.20"), rules=True)
    assert sorted(pv.instances(c, rules=True)) == ["in-6980"]


def test_off_entries_have_no_instance():
    c = pv.add_entry(pv.default_config(), "in", entry(on=False), rules=False)
    assert pv.instances(c, rules=False) == {}
    c = pv.add_entry(pv.default_config(), "in", entry(on=False), rules=True)
    assert pv.instances(c, rules=True) == {}


def test_listener_conf_on_1_0():
    e = entry(target="alsa_output.gcs8", port=6981, latency=20)
    text = pv.render_listener([e], rules=False)
    assert text.startswith("# Generated by PipeWrench")
    for want in ("libpipewire-module-vban-recv", 'source.ip = "0.0.0.0"', "source.port = 6981",
                 "sess.latency.msec = 20", 'audio.format = "S16LE"', "audio.rate = 48000", "audio.channels = 2",
                 "audio.position = [ FL FR ]", 'node.name = "pipewrench-vban-in-studio"', 'node.description = "Studio"',
                 'target.object = "alsa_output.gcs8"', "node.autoconnect = true"):
        assert want in text, want
    assert "stream.rules" not in text


def test_listener_unconnected_does_not_autoconnect():
    text = pv.render_listener([entry()], rules=False)
    assert "node.autoconnect = false" in text and "target.object" not in text


def test_listener_with_rules_matches_each_ip():
    a, b = entry(), entry(name="Desk", ip="192.168.1.20", id="desk", target="alsa_output.x")
    text = pv.render_listener([a, b], rules=True)
    assert "stream.rules" in text
    assert 'vban.ip = "192.168.1.86"' in text and 'vban.ip = "192.168.1.20"' in text
    assert text.count("create-stream") == 2
    assert 'node.name = "pipewrench-vban-in-desk"' in text


def test_listener_midi():
    text = pv.render_listener([entry(media="midi")], rules=False)
    assert 'sess.media = "midi"' in text and "audio.format" not in text


def test_sender_conf_is_a_low_priority_output_device():
    e = pv.new_entry("out", "Studio", "192.168.1.86")
    text = pv.render_sender(e)
    for want in ("libpipewire-module-vban-send", 'destination.ip = "192.168.1.86"', "destination.port = 6980",
                 'sess.name = "Studio"', 'media.class = "Audio/Sink"', "priority.session = 1",
                 'node.name = "pipewrench-vban-out-studio"', 'node.description = "Studio"', 'audio.format = "S16LE"'):
        assert want in text, want


def test_sender_midi():
    text = pv.render_sender({**pv.new_entry("out", "Pad", "10.0.0.1"), "media": "midi"})
    assert 'sess.media = "midi"' in text and "Audio/Sink" not in text and "audio.rate" not in text


def test_mono_position():
    assert "audio.position = [ MONO ]" in pv.render_sender({**pv.new_entry("out", "A", "10.0.0.1"), "channels": 1})


def test_unit_template_runs_pipewire_on_the_instance_conf(tmp_path):
    text = pv.render_unit("/usr/bin/pipewire", tmp_path / "vban")
    assert "Generated by PipeWrench" in text
    assert f"ExecStart=/usr/bin/pipewire -c {tmp_path / 'vban'}/%i.conf" in text
    assert "WantedBy=pipewire.service" in text and "PartOf=pipewire.service" in text


def test_node_names():
    assert pv.node_name("in", "studio") == "pipewrench-vban-in-studio"
    assert pv.node_name("out", "studio") == "pipewrench-vban-out-studio"


def test_instance_of_entry():
    assert pv.instance_of("in", entry(port=6981), rules=False) == "in-studio"
    assert pv.instance_of("in", entry(port=6981), rules=True) == "in-6981"
    assert pv.instance_of("out", pv.new_entry("out", "Studio", "1.2.3.4"), rules=True) == "out-studio"


def test_unit_name():
    assert pv.unit("in-studio") == "pipewrench-vban@in-studio.service"


# --- sync plan -----------------------------------------------------------------

def test_plan_starts_new_and_stops_gone():
    want = {"in-studio": "A", "out-pc": "B"}
    have = {"in-studio": "A", "in-old": "C"}
    plan = pv.plan(want, have, active={"in-studio"})
    assert plan.write == {"out-pc": "B"}
    assert plan.remove == ["in-old"]
    assert plan.start == ["out-pc"] and plan.restart == [] and plan.stop == ["in-old"]


def test_plan_restarts_a_changed_conf_and_starts_a_stopped_one():
    plan = pv.plan({"in-studio": "new", "out-pc": "B"}, {"in-studio": "old", "out-pc": "B"}, active={"in-studio"})
    assert plan.write == {"in-studio": "new"}
    assert plan.restart == ["in-studio"] and plan.start == ["out-pc"]


def test_plan_commands():
    plan = pv.Plan(write={}, remove=["in-old"], start=["out-pc"], restart=["in-studio"], stop=["in-old"])
    assert pv.plan_cmds(plan) == [
        ["systemctl", "--user", "disable", "--now", "pipewrench-vban@in-old.service"],
        ["systemctl", "--user", "enable", "--now", "pipewrench-vban@out-pc.service"],
        ["systemctl", "--user", "restart", "pipewrench-vban@in-studio.service"]]


def test_active_from_systemctl():
    out = ("pipewrench-vban@in-studio.service loaded active running PipeWrench VBAN in-studio\n"
           "pipewrench-vban@out-pc.service loaded failed failed PipeWrench VBAN out-pc\n")
    assert pv.parse_units(out) == {"in-studio": "active", "out-pc": "failed"}


def test_status_words():
    assert pv.status_word(entry(), {"in-studio": "active"}, rules=False) == "on"
    assert pv.status_word(entry(), {"in-studio": "failed"}, rules=False) == "failed"
    assert pv.status_word(entry(), {}, rules=False) == "stopped"
    assert pv.status_word(entry(on=False), {}, rules=False) == "off"


# --- the far end's instructions -----------------------------------------------------

def test_point_here():
    assert pv.point_here(entry(port=6981), "192.168.1.99") == "sends to 192.168.1.99:6981"


def test_midi_incoming_target_is_not_target_object():
    text = pv.render_listener([entry(media="midi", target="KB16 MIDI 1")], rules=False)
    assert "target.object" not in text and "node.autoconnect = false" in text


# --- links PipeWrench keeps -------------------------------------------------------

import pwpatch as pp  # noqa: E402


def _graph():
    P = pp.Port
    brave = pp.Endpoint(10, False, "Brave", "Brave", "app", (P(1, 10, "output_FL", "FL", "output", False),
                                                            P(2, 10, "output_FR", "FR", "output", False)))
    send = pp.Endpoint(20, False, "Studio", "pipewrench-vban-out-studio", "device",
                       (P(3, 20, "send_FL", "FL", "input", False), P(4, 20, "send_FR", "FR", "input", False)))
    mon = pp.Endpoint(30, True, "GCS-8 (monitor)", "alsa_output.gcs8", "device",
                      (P(5, 30, "monitor_FL", "FL", "output", True), P(6, 30, "monitor_FR", "FR", "output", True)))
    sink = pp.Endpoint(30, False, "GCS-8", "alsa_output.gcs8", "device",
                       (P(7, 30, "playback_FL", "FL", "input", False), P(8, 30, "playback_FR", "FR", "input", False)))
    recv = pp.Endpoint(40, False, "Pad", "pipewrench-vban-in-pad", "device", (P(9, 40, "receive_1", "1", "output", False),), "midi")
    reaper = pp.Endpoint(50, False, "REAPER", "REAPER MIDI in", "app", (P(11, 50, "in1", "1", "input", False),), "midi")
    return pp.Graph([brave, mon, recv], [send, sink, reaper], {})


def test_outgoing_source_is_linked():
    c = pv.add_entry(pv.default_config(), "out", {**pv.new_entry("out", "Studio", "10.0.0.1"), "target": "Brave"}, rules=False)
    assert pv.link_cmds(c, _graph()) == [["pw-link", "1", "3"], ["pw-link", "2", "4"]]


def test_outgoing_monitor_source():
    c = pv.add_entry(pv.default_config(), "out",
                     {**pv.new_entry("out", "Studio", "10.0.0.1"), "target": "monitor:alsa_output.gcs8"}, rules=False)
    assert pv.link_cmds(c, _graph()) == [["pw-link", "5", "3"], ["pw-link", "6", "4"]]


def test_links_skip_what_is_missing_off_or_audio_incoming():
    c = pv.default_config()
    c = pv.add_entry(c, "out", {**pv.new_entry("out", "Studio", "10.0.0.1"), "target": "Gone"}, rules=False)
    c = pv.add_entry(c, "in", {**pv.new_entry("in", "Studio", "10.0.0.1"), "target": "alsa_output.gcs8"}, rules=False)
    assert pv.link_cmds(c, _graph()) == []
    c = pv.add_entry(pv.default_config(), "out", {**pv.new_entry("out", "Studio", "10.0.0.1"), "target": "Brave", "on": False},
                     rules=False)
    assert pv.link_cmds(c, _graph()) == []


def test_incoming_midi_target_is_linked():
    c = pv.add_entry(pv.default_config(), "in",
                     {**pv.new_entry("in", "Pad", "10.0.0.1"), "media": "midi", "target": "REAPER MIDI in"}, rules=False)
    assert pv.link_cmds(c, _graph()) == [["pw-link", "9", "11"]]


def test_endpoints_for_leave_out_our_own_end():
    g = _graph()
    assert [x.label for x in pv.endpoints_for(g, "out", "audio")] == ["Brave", "GCS-8 (monitor)"]
    assert [x.label for x in pv.endpoints_for(g, "in", "audio")] == ["Studio", "GCS-8"]  # an outgoing stream is a fine destination
    assert [x.label for x in pv.endpoints_for(g, "in", "midi")] == ["REAPER"]
    assert pv.endpoints_for(None, "in", "audio") == []
    assert pv.endpoint_key(g.sources[1]) == "monitor:alsa_output.gcs8"


def test_keeps_links():
    c = pv.add_entry(pv.default_config(), "in", {**pv.new_entry("in", "A", "10.0.0.1"), "target": "x"}, rules=False)
    assert not pv.keeps_links(c)  # audio incoming links itself
    assert pv.keeps_links(pv.update_entry(c, "in", "a", media="midi"))
    c = pv.add_entry(pv.default_config(), "out", {**pv.new_entry("out", "A", "10.0.0.1"), "target": "x"}, rules=False)
    assert pv.keeps_links(c) and not pv.keeps_links(pv.update_entry(c, "out", "a", on=False))


def test_unlink_the_old_source():
    g = _graph()
    g.links = {(1, 3): 100, (2, 4): 101}
    old = {**pv.new_entry("out", "Studio", "10.0.0.1"), "target": "Brave"}
    assert pv.unlink_cmds("out", old, g) == [["pw-link", "-d", "1", "3"], ["pw-link", "-d", "2", "4"]]
    assert pv.unlink_cmds("in", {**pv.new_entry("in", "A", "10.0.0.1"), "target": "alsa_output.gcs8"}, g) == []
    assert pv.unlink_cmds("out", {**old, "target": None}, g) == []


def test_incoming_manual_matches_by_ip_alone():
    """With stream.rules, a typed-in sender must not be matched by its display name (Skrappjaw, 2026-10-26: the Studio
    came back as "desktop")."""
    e = pv.new_entry("in", "Studio", "192.168.1.86")
    assert e["stream"] == ""
    text = pv.render_listener([e], rules=True)
    assert 'vban.ip = "192.168.1.86"' in text and "sess.name" not in text


def test_outgoing_is_named_after_this_machine():
    assert pv.new_entry("out", "desktop", "192.168.1.99", "studio-pc")["stream"] == "studio-pc"
    assert 'sess.name = "studio-pc"' in pv.render_sender(pv.new_entry("out", "desktop", "192.168.1.99", "studio-pc"))


def test_from_heard_outgoing_sends_back_under_this_host():
    e = pv.from_heard("out", pv.Heard("Studio", "audio", 48000, 2, "S16LE"), "192.168.1.86", "desktop")
    assert (e["name"], e["stream"]) == ("Studio", "desktop")
    e = pv.from_heard("in", pv.Heard("studio-pc", "audio", 48000, 2, "S16LE"), "192.168.1.86", "desktop")
    assert (e["name"], e["stream"]) == ("studio-pc", "studio-pc")
