"""Settings → Audio rework (Skrappjaw, 2026-10-26): "Even though its set to not force rate or buffer, theres no
indicator for the current setting. ALSO theres no indicator as to the difference between pipewire and the
devices and what that means." Then: words "PipeWire & Devices"; "Every card with status. show off if off";
"visually break up what is on screen". Plan: notes/audio-settings-plan.md."""
from pathlib import Path

import pwcore as pc
import pwinventory as pi
import pwrates as pr

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
FREE = {**CUR, "clock.force-rate": 0, "clock.force-quantum": 0}       # nothing forced


def top(state, quant, rate, name, follower=False):
    return pc.TopRow(state, 50, quant, rate, "0.1ms", "0.1ms", "0.01", "0.01", 0, "F32P 2 48000", name, follower)


RUNNING = [top("R", 256, 48000, "alsa_output.usb-BOSS_GCS-8-01"), top("R", 256, 48000, "Brave", True),
           top("S", 0, 0, "alsa_output.pci-hdmi")]


def cards():
    return [pr.CardRates("GCS-8", pr.Stream(frozenset({48000}), (), 48000), status="running", live=48000),
            pr.CardRates("C920 webcam", pr.Stream(frozenset({16000, 32000}), ()), status="running", live=32000),
            pr.CardRates("UMC404HD", pr.Stream(frozenset({44100, 48000}), ()), status="suspended"),
            pr.CardRates("HDMI", None, status="off")]


def audio(**kw):
    return pc.UIState(tab="settings", settings_cat="audio", settings_focus="pane", **kw)


def screen(st, current=FREE, block=RUNNING, rates=None, height=80):
    return pc.render_screen(st, current, block, 100, height, rates=cards() if rates is None else rates)[0]


def pane(st, **kw):
    return [t.split("│", 1)[1] for t, _ in screen(st, **kw) if "│" in t]


def key(st, *keys, current=FREE):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, current)
        actions += a
    return st, actions


# --- row order follows the screen: overrides, limits, buttons, then Advanced → Log level -----------------

def test_rows_in_screen_order():
    assert [s.label for s in pc.SETTINGS] == ["Force sample rate", "Force buffer size", "Minimum buffer",
                                              "Max buffer", "Log level"]
    assert pc.CLOCK_BUTTONS_ROW == 4 and pc.LOG_ROW == 5
    assert pc.audio_setting(1).key == "clock.force-quantum" and pc.audio_setting(pc.LOG_ROW).key == "log.level"
    assert pc.audio_setting(pc.CLOCK_BUTTONS_ROW) is None


def test_log_level_sits_below_the_buttons_and_applies_on_enter():
    st, _ = key(audio(cursor=pc.CLOCK_BUTTONS_ROW), "down")
    assert st.cursor == pc.LOG_ROW and key(st, "down")[0].cursor == pc.LOG_ROW
    st, _ = key(st, "enter", "right")
    assert st.pending == {"log.level": 3}
    st, actions = key(st, "enter")
    assert actions == [("set_many", [("log.level", 3)])] or actions[0][0] == "set_many"


# --- what PipeWire is running now ----------------------------------------------------------------------

def test_pipewire_now_reads_the_running_drivers():
    assert pc.pipewire_now(RUNNING) == [(256, 48000, "alsa_output.usb-BOSS_GCS-8-01")]
    assert pc.pipewire_now([top("S", 0, 0, "x")]) == [] and pc.pipewire_now(None) is None


def test_running_now_line_and_where_it_comes_from():
    text = "\n".join(pane(audio()))
    assert "Running now" in text and "48 kHz · 256 samples · 5.33 ms" in text
    assert "buffer: smallest an app asks for" in text and "rate: PipeWire's default" in text
    forced = "\n".join(pane(audio(), current=CUR))                 # the fixture forces 48000 / 128
    assert "buffer: forced" in forced and "rate: forced" in forced


def test_idle_says_what_applies_when_something_plays():
    text = "\n".join(pane(audio(), block=[top("S", 0, 0, "x")]))
    assert "idle" in text and "48 kHz · 1024 samples" in text       # clock.rate / clock.quantum defaults


def test_force_rows_show_off_and_the_value_running():
    rows = pane(audio())
    rate = next(t for t in rows if "Force sample rate" in t)
    buf = next(t for t in rows if "Force buffer size" in t)
    assert "off" in rate and "→ 48 kHz now" in rate
    assert "off" in buf and "→ 256 now" in buf


# --- boxes ---------------------------------------------------------------------------------------------

def test_the_page_is_broken_into_boxes():
    text = pane(audio())
    tops = [t.strip().split("─ ", 1)[1].split(" ─")[0] for t in text if t.strip().startswith("┌─ ")]
    assert tops == ["PipeWire", "Devices", "Realtime", "Advanced"]
    assert sum(1 for t in text if t.strip().startswith("└")) == 4


def test_every_card_with_its_status_and_how_it_meets_pipewire():
    rows = pane(audio())
    line = lambda label: next(t for t in rows if label in t)
    assert "running" in line("GCS-8") and "48 kHz" in line("GCS-8") and "same as PipeWire" in line("GCS-8")
    assert "32 kHz" in line("C920") and "resampled" in line("C920")
    assert "suspended" in line("UMC404HD")
    assert "off" in line("HDMI") and "resampled" not in line("HDMI")


def test_cursor_rows_scroll_into_view_inside_boxes():
    rows = [t for t, _ in screen(audio(cursor=pc.LOG_ROW), height=24)]
    assert any("> Log level" in t for t in rows)


def test_audio_rows_explain_themselves_on_the_status_line():
    assert "PipeWire's rate" in screen(audio(cursor=0), height=40)[-1][0]
    assert "log" in screen(audio(cursor=pc.LOG_ROW), height=40)[-1][0].lower()


# --- every card, with status, from the inventory -------------------------------------------------------

def card(label, alsa_card, active="HiFi", states=(), rate=None):
    rows = [pi.Row(id=n, name=f"{label}{n}", label=label, direction="out", kind="hardware", state=s, rate=rate)
            for n, s in enumerate(states)]
    return pi.Card(id=alsa_card + 40, name=label, label=label, profiles=[], active=active, rows=rows,
                   alsa_card=alsa_card)


def test_read_keeps_off_cards_and_gives_each_a_status(tmp_path):
    out = pr.read([card("A", 0, states=("running", "suspended"), rate=48000), card("B", 1, states=("idle",)),
                   card("C", 2, states=("suspended",)), card("D", 3, active="off")], root=tmp_path)
    assert [(c.label, c.status, c.live) for c in out] == [("A", "running", 48000), ("B", "idle", None),
                                                          ("C", "suspended", None), ("D", "off", None)]


def test_off_cards_never_warn():
    off = [pr.CardRates("HDMI", None, status="off"), pr.CardRates("GCS-8", pr.Stream(frozenset({48000})), status="idle")]
    assert pr.warnings(48000, [48000], 48000, off) == []


# --- found on Skrappjaw's machine, 2026-10-26 ---------------------------------------------------------------

def test_drivers_that_agree_show_one_running_line():
    block = [top("R", 1024, 48000, "alsa_output.usb-BOSS_GCS-8-01.HiFi__out"),
             top("R", 1024, 48000, "alsa_input.usb-BOSS_GCS-8-01.HiFi__in")]
    rows = [t for t in pane(audio(), block=block) if "samples ·" in t]
    assert len(rows) == 1 and "alsa_" not in rows[0]
    block[1] = top("R", 256, 48000, "alsa_input.usb-BOSS_GCS-8-01.HiFi__in")
    rows = [t for t in pane(audio(), block=block) if "samples ·" in t]
    assert len(rows) == 2 and all(len(t) <= 85 for t in rows)          # they differ: each names its driver
    named = [pr.CardRates("GCS-8", None, "running", 48000,
                          nodes=(("alsa_input.usb-BOSS_GCS-8-01.HiFi__in", "GCS-8 CH 2"),))]
    rows = [t for t in pane(audio(), block=block, rates=named) if "samples ·" in t]
    assert "GCS-8 CH 2" in rows[1]                                       # by its label, not its node name


def test_a_card_with_different_rates_each_way_says_so():
    s = pr.parse_stream("X\n\nPlayback:\n    Rates: 32000\n\nCapture:\n    Rates: 16000\n")
    assert s.rates == frozenset() and pr.describe(s) == "out 32k · in 16k"   # still no common rate to force
