"""Device sample rates from the kernel (/proc/asound/cardN/stream*), and the clock warnings built on them."""
from pathlib import Path

import pwcore as pc
import pwinventory as pi
import pwrates as pr

FIX = Path(__file__).parent / "fixtures" / "rates"
UMC_RATES = {44100, 48000, 88200, 96000, 176400, 192000}


def proc_tree(tmp_path, cards):
    """A fake /proc/asound: {card number: fixture file name or None (no stream file, as for PCI cards)}."""
    for n, fixture in cards.items():
        d = tmp_path / f"card{n}"
        d.mkdir()
        if fixture:
            (d / "stream0").write_text((FIX / fixture).read_text())
    return tmp_path


def card(label, alsa_card, active="HiFi"):
    return pi.Card(id=alsa_card + 40, name=label, label=label, profiles=[], active=active, alsa_card=alsa_card)


def test_parse_umc404hd_lists_every_rate_and_what_it_runs_at():
    s = pr.parse_stream((FIX / "umc404hd-stream0.txt").read_text())
    assert s.rates == frozenset(UMC_RATES)
    assert s.running == 48000


def test_parse_gcs8_is_48k_only():
    assert pr.parse_stream((FIX / "gcs8-stream0.txt").read_text()).rates == frozenset({48000})


def test_a_rate_must_work_in_every_direction():
    text = "X\n\nPlayback:\n    Rates: 44100, 48000\n\nCapture:\n    Rates: 48000\n"
    assert pr.parse_stream(text).rates == frozenset({48000})


def test_continuous_ranges():
    s = pr.parse_stream("X\n\nPlayback:\n    Rates: 8000 - 96000 (continuous)\n")
    assert s.supports(44100) and s.supports(96000) and not s.supports(192000)


def test_cards_are_matched_by_alsa_card_number_and_off_cards_kept_as_off(tmp_path):
    root = proc_tree(tmp_path, {1: "umc404hd-stream0.txt", 0: None, 5: "gcs8-stream0.txt"})
    got = pr.read([card("UMC404HD", 1), card("Built-in Audio", 0), card("GCS-8", 5, active="off")], root)
    assert [(c.label, c.known) for c in got] == [("UMC404HD", True), ("Built-in Audio", False), ("GCS-8", False)]
    assert got[2].status == "off"                                  # listed as off, its rates never read
    assert got[0].supports(192000) and got[0].running == 48000
    assert got[1].supports(192000) is None


def test_inventory_cards_carry_their_alsa_card_number():
    def device(i, **props):
        return {"id": i, "type": "PipeWire:Interface:Device",
                "info": {"props": {"media.class": "Audio/Device", "device.name": f"alsa_card.{i}", **props}}}

    cards = pi.inventory([device(40, **{"api.alsa.card": 1}), device(41, **{"api.alsa.card": "5"}),
                          device(42)]).cards
    assert {c.id: c.alsa_card for c in cards} == {40: 1, 41: 5, 42: None}


def test_khz():
    assert pr.khz(44100) == "44.1k" and pr.khz(48000) == "48k" and pr.khz(176400) == "176.4k"


CUR = {"clock.rate": 48000, "clock.allowed-rates": [48000], "clock.force-rate": 0,
       "clock.min-quantum": 32, "clock.max-quantum": 2048, "clock.force-quantum": 0}


def test_a_device_that_cannot_run_the_rate_is_named(tmp_path):
    root = proc_tree(tmp_path, {1: "umc404hd-stream0.txt", 5: "gcs8-stream0.txt"})
    devices = pr.read([card("UMC404HD", 1), card("GCS-8", 5)], root)
    w = pc.validate({**CUR, "clock.allowed-rates": [48000, 192000]}, {"clock.force-rate": 192000}, devices)
    assert w == ["GCS-8 can't run at 192k (it supports 48k)"]


def test_a_rate_outside_allowed_rates_says_devices_stay_and_resample(tmp_path):
    devices = pr.read([card("UMC404HD", 1)], proc_tree(tmp_path, {1: "umc404hd-stream0.txt"}))
    w = pc.validate(CUR, {"clock.force-rate": 192000}, devices)
    assert w == ["192k isn't in allowed rates (48k), so devices stay at 48k and PipeWire resamples"]


def test_devices_without_a_rate_list_get_the_generic_line(tmp_path):
    """Skrappjaw's wording, 2026-10-25. Non-USB cards don't report rates."""
    devices = pr.read([card("Built-in Audio", 0)], proc_tree(tmp_path, {0: None}))
    w = pc.validate({**CUR, "clock.allowed-rates": [48000, 96000]}, {"clock.force-rate": 96000}, devices)
    assert w == ["Make sure your device(s) support the rate selected."]
    assert pc.validate({**CUR, "clock.allowed-rates": [48000, 96000]}, {"clock.force-rate": 96000}) == \
        ["Make sure your device(s) support the rate selected."]  # rates not read at all


def test_no_rate_warnings_without_a_forced_rate(tmp_path):
    devices = pr.read([card("GCS-8", 5)], proc_tree(tmp_path, {5: "gcs8-stream0.txt"}))
    assert pc.validate(CUR, {"clock.force-quantum": 256}, devices) == []
    assert pc.validate(CUR, {"clock.force-rate": 0}, devices) == []


def test_settings_lists_device_rates_and_names_the_one_that_cannot(tmp_path):
    root = proc_tree(tmp_path, {1: "umc404hd-stream0.txt", 5: "gcs8-stream0.txt", 0: None})
    rates = pr.read([card("UMC404HD", 1), card("GCS-8", 5), card("Built-in Audio", 0)], root)
    ids = pc.audio_rows(None, rates)
    st = pc.UIState(tab="settings", settings_focus="pane", cursor=ids.index(("setting", 0)),
                    pending={"clock.force-rate": 192000})
    cur = {**CUR, "clock.allowed-rates": [48000, 192000]}
    lines, _ = pc.render_screen(st, cur, None, 100, 50, rates=rates)
    body = "\n".join(t for t, _ in lines)
    umc = next(t for t in body.splitlines() if "UMC404HD" in t)
    assert "48 kHz" in umc and "Built-in Audio" in body
    note = lambda label: pc.render_screen(pc.dataclasses.replace(st, cursor=ids.index(("device", label))), cur, None,
                                          100, 50, rates=rates)[0][-1][0]   # what a card can run: its status line
    assert "44.1 48 88.2 96 176.4 192k" in note("UMC404HD") and "not reported" in note("Built-in Audio")
    assert "GCS-8 can't run at 192k" in body
    assert "Make sure your device(s) support the rate selected." in body


def test_settings_warnings_wrap_instead_of_clipping(tmp_path):
    rates = pr.read([card("UMC404HD", 1)], proc_tree(tmp_path, {1: "umc404hd-stream0.txt"}))
    st = pc.UIState(tab="settings", settings_focus="pane", cursor=0, pending={"clock.force-rate": 192000})
    lines, _ = pc.render_screen(st, CUR, None, 60, 50, rates=rates)
    body = " ".join(t.strip(" │") for t, _ in lines)
    assert "PipeWire resamples" in body
