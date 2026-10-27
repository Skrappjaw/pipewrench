"""Settings → Audio rework 2 (Skrappjaw, 2026-10-26): "some things are backwards and the navigation breaks with so
many things being not interactable." Order: what the thing is, then its status, by importance; PipeWire is the
server that manages the devices. Every row selectable; boxes kept (mockup B). Plan: notes/audio-settings-plan.md
"Rework 2"."""
import pwcore as pc
import pwrates as pr
import pwrealtime as prt

from test_audio_page import CUR, FREE, RUNNING, cards, top


def realtime(ok):
    proc = prt.Proc(4242, "pipewire", 1800, 95 if ok else 0,
                    tuple(prt.Thread(4250 + k, 1 if ok else 0, 88 if ok else 0) for k in range(2)))
    return prt.Status("user", {"pipewire": 95}, True, (proc,), 0 if ok else 12)


def rows(rt=None, rates=None):
    return pc.audio_rows(rt, cards() if rates is None else rates)


def audio(rt=None, rates=None, rid=None, **kw):
    r = rows(rt, rates)
    if rid is not None:
        kw["cursor"] = r.index(rid)
    return pc.UIState(tab="settings", settings_cat="audio", settings_focus="pane", audio_rows=r, **kw)


def screen(st, rt=None, rates=None, current=FREE, block=RUNNING, height=80, width=100):
    return pc.render_screen(st, current, block, width, height, realtime=rt,
                            rates=cards() if rates is None else rates)[0]


def pane(st, **kw):
    return [t.split("│", 1)[1] for t, _ in screen(st, **kw) if "│" in t]


def key(st, *keys, current=FREE):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, current)
        actions += a
    return st, actions


def cursor_line(st, **kw):
    inner = [t.split("│", 2)[2][1:] for t, _ in screen(st, **kw) if t.count("│") >= 2]  # past the box margin
    return next(t for t in inner if t.startswith("> "))


# --- the order: the server, what it manages, what you change, then Advanced -----------------------------

def test_boxes_run_server_devices_clock_advanced():
    tops = [t.strip().split("─ ", 1)[1].split(" ─")[0] for t in pane(audio()) if t.strip().startswith("┌─ ")]
    assert tops == ["PipeWire", "Devices", "Clock", "Advanced"]


def test_row_ids_in_screen_order():
    r = rows(realtime(False))
    assert r[:2] == (("server",), ("realtime",))
    assert [x for x in r if x[0] == "rt"] == [("rt", "pipewire group"), ("rt", "realtime limit"),
                                             ("rt", "audio threads"), ("rt", "portal refusals")]
    assert [x for x in r if x[0] == "fix"] == [("fix", 0), ("fix", 1)]
    assert r.index(("now",)) > r.index(("fix", 1))
    assert [x[1] for x in r if x[0] == "device"] == ["GCS-8", "C920 webcam", "UMC404HD", "HDMI"]
    assert r[-8:] == (("setting", 0), ("setting", 1), ("setting", 2), ("setting", 3), ("allowed",), ("defaults",),
                      ("buttons",), ("log",))


def test_realtime_ok_has_no_fix_rows_and_nothing_read_has_one_row_each():
    assert not [x for x in rows(realtime(True)) if x[0] == "fix"]
    bare = pc.audio_rows()
    assert [x for x in bare if x[0] in ("rt", "fix")] == [] and [x for x in bare if x[0] == "device"] == [("device", None)]


# --- every row is reachable, in order, and scrolls into view ----------------------------------------------

def test_down_walks_every_row_in_screen_order_on_a_short_screen():
    rt = realtime(False)
    st = audio(rt, cursor=0)
    seen = []
    for _ in range(len(st.audio_rows)):
        seen.append(cursor_line(st, rt=rt, height=24).strip())
        st, _ = key(st, "down")
    assert len(seen) == len(st.audio_rows) and len(set(seen)) == len(seen)   # each row its own line, all seen
    assert seen[0].startswith("> Server") and seen[-1].startswith("> Log level")
    assert key(st, "down")[0].cursor == len(st.audio_rows) - 1               # stops at the last row


def test_every_line_inside_a_box_is_a_row_except_headers_and_frames():
    rt = realtime(False)
    st = audio(rt, cursor=-1)
    lines, hits = pc.render_screen(st, FREE, RUNNING, 100, 80, realtime=rt, rates=cards())
    hit_rows = {h[0] for h in hits if h[3][0] in ("setting", "clock_button")}
    for n, (text, _) in enumerate(lines):
        inner = text.split("│", 2)[2] if text.count("│") >= 2 else ""
        body = inner.rstrip(" │").strip()
        if not body or body.startswith(("Device ", "!")):
            continue
        assert n in hit_rows, text


def test_cursor_follows_its_row_when_a_card_appears():
    st = audio(rid=("setting", 1))
    more = cards() + [pr.CardRates("Scarlett", None, status="idle")]
    st = pc.with_audio_rows(st, pc.audio_rows(None, [more[-1]] + more[:-1]))
    assert st.audio_rows[st.cursor] == ("setting", 1)


# --- the server first: what it is, then its status ---------------------------------------------------------

def test_server_row_says_running_or_not_answering():
    assert "● running" in pane(audio())[1]
    down = pc.dataclasses.replace(audio(), pw_down="PipeWire not answering")
    assert "✖ not answering" in "\n".join(pane(down))


def test_realtime_row_then_its_checks_then_the_fix():
    text = "\n".join(pane(audio(realtime(False)), rt=realtime(False)))
    assert "Realtime" in text and "◐ not realtime" in text
    assert "realtime limit" in text and "for now" in text and "prlimit" in text
    ok = "\n".join(pane(audio(realtime(True)), rt=realtime(True)))
    assert "● realtime" in ok and "prlimit" not in ok


def test_running_now_and_what_set_it_are_rows():
    text = "\n".join(pane(audio()))
    assert "Running now" in text and "48 kHz · 256 samples · 5.33 ms" in text
    assert "buffer set by" in text and "rate set by" in text


# --- read-only rows: nothing opens, the legend says so, the status line explains -------------------------

def test_enter_on_a_read_only_row_does_nothing():
    st, actions = key(audio(rid=("device", "GCS-8")), "enter")
    assert actions == [] and not st.editing


def test_value_rows_say_enter_changes_them_and_read_only_rows_dont():
    note = lambda rid: screen(audio(rid=rid), height=60)[-1][0]
    assert "Enter" not in note(("server",)) and "Enter to change" in note(("setting", 0))


def test_status_line_explains_the_row():
    note = lambda rid, **kw: screen(audio(rid=rid), height=60, **kw)[-1][0]
    assert "server" in note(("server",))
    assert "straight through" in note(("device", "GCS-8")) and "48k" in note(("device", "GCS-8"))
    assert "resamples" in note(("device", "C920 webcam"))
    assert "Apply" in note(("buttons",)) and "Restart" in note(("buttons",))
    assert "PipeWire's rate" in note(("setting", 0))


def test_no_explanation_prose_left_on_the_page():
    text = "\n".join(pane(audio()))
    assert "Apply: live now" not in text and "Overrides ·" not in text and "Limits ·" not in text


# --- values ------------------------------------------------------------------------------------------------

def test_off_override_before_pw_top_answers_waits_rather_than_guessing():
    line = next(t for t in pane(audio(), block=None) if "Force buffer size" in t)
    assert "waiting for pw-top" in line and "ms" not in line


def test_clock_rows_still_edit_and_apply():
    st, actions = key(audio(rid=("setting", 1)), "enter", "right", "enter")
    assert actions and actions[0][0] == "set_many"


def test_buttons_still_press():
    st, actions = key(audio(rid=("buttons",), clock_button=1), "enter")
    assert actions == [("save_clock",)]


def test_log_level_is_the_last_row_and_edits():
    st, _ = key(audio(rid=("log",)), "enter", "right")
    assert st.pending == {"log.level": 3}


def test_legend_and_note_follow_the_drawn_rows_without_audio_rows_set():
    st = pc.UIState(tab="settings", settings_cat="audio", settings_focus="pane",
                    cursor=rows(realtime(False)).index(("setting", 1)))      # audio_rows left empty
    lines = screen(st, rt=realtime(False))
    assert "Enter" in "\n".join(t for t, _ in lines[-5:]) and "PipeWire's buffer" in lines[-1][0]


def test_a_wrapped_row_shows_whole_on_a_short_narrow_screen():
    rt = realtime(False)
    lines = [t for t, _ in screen(audio(rt, rid=("fix", 1)), rt=rt, height=24, width=60)]
    assert any("> " in t and "for now" in t for t in lines) and any("wireplumber" in t for t in lines)
