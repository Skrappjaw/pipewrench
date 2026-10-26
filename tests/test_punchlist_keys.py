"""Punch list, 2026-10-26: Key Bindings page with view headings, fixed Global keys and a MIDI/CC column; the
Display notes move to the status bar; no shortcuts for saving or restarting WirePlumber ("Those are in menus
already where they need to be")."""
from pathlib import Path

import pytest

import pwcore as pc
import pwkeys as pk

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())


def prefs(**kw):
    return pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane", **kw)


def key(st, *keys):
    actions = []
    for k in keys:
        st, a = pc.handle_key(st, k, CUR)
        actions += a
    return st, actions


def lines(st, height=80):
    return pc.render_screen(st, CUR, "", 100, height)[0]


def body(st, height=80):
    """The Settings pane's lines, without the category menu on the left."""
    return [t.split("│", 1)[1] for t, _ in lines(st, height) if "│" in t]


def row_of(kind, ident):
    return pc.KEYS_ROW + next(n for n, (k, x) in enumerate(pc.BINDING_ROWS)
                              if k == kind and (x.id if kind == "action" else x[0]) == ident)


# --- no WirePlumber shortcuts ----------------------------------------------------------------------

def test_no_save_or_restart_actions():
    ids = {a.id for a in pk.ACTIONS}
    assert not ids & {"settings.save", "settings.restart", "devices.restart"}
    assert pk.clean({"settings.save": "z", "devices.restart": "Q", "patch.grid": "G"}) == {"patch.grid": "G"}


@pytest.mark.parametrize("st", [pc.UIState(tab="settings", settings_focus="pane"),
                                pc.UIState(tab="settings", settings_focus="pane", cursor=pc.audio_index(("buttons",)))])
def test_s_and_R_do_nothing_on_settings(st):
    for k in ("s", "R"):
        after, actions = key(st, k)
        assert actions == [] and after.popup is None
    chips = [k for k, _ in pc.legend(st)]
    assert "s" not in chips and "R" not in chips


def test_R_does_nothing_on_devices():
    assert pc.handle_key(pc.UIState(tab="devices"), "R", CUR, devices=None)[0].popup is None
    assert "R" not in [k for k, _ in pc.legend(pc.UIState(tab="devices"))]


def test_save_and_restart_buttons_still_work():
    st = pc.UIState(tab="settings", settings_focus="pane", cursor=pc.audio_index(("buttons",)))
    save = pc.CLOCK_BUTTONS.index("Save")
    assert key(st, *["right"] * save, "enter")[1] == [("save_clock",)]
    after, _ = key(st, *["right"] * pc.CLOCK_BUTTONS.index("Restart"), "enter")
    assert after.popup == ("restart",)


# --- Key Bindings page -----------------------------------------------------------------------------

def test_key_bindings_page_layout():
    text = body(prefs())
    assert any(t.strip() == "Key Bindings" for t in text) and not any(t.strip() == "Keys" for t in text)
    head = next(t for t in text if "Action" in t and "MIDI/CC" in t)
    assert head.index("Action") < head.index("Key") < head.index("MIDI/CC")
    order = [t.strip() for t in text if t.strip() in ("Global", "Mixer", "Devices", "Patch Bay")]
    assert order == ["Global", "Mixer", "Devices", "Patch Bay"]
    assert not any("Settings ·" in t or "Devices ·" in t for t in text)
    assert any(t.strip().startswith("Show hidden") for t in text)                 # labels read like Global's


def test_global_rows_are_fixed_keys():
    assert [label for _, label, _ in pk.FIXED] == ["Next View", "Prev View", "Mixer", "Devices", "Patch Bay",
                                                   "Settings", "Top", "About"]
    assert [k for _, _, k in pk.FIXED] == ["Tab", "⇧Tab", "1", "2", "3", "4", "5", "6"]
    text = body(prefs())
    nxt = next(t for t in text if "Next View" in t)
    assert "Tab" in nxt and "—" in nxt
    st, actions = key(prefs(cursor=row_of("fixed", "global.next")), "enter")
    assert st.key_capture is None and actions == [] and "fixed" in st.message


def test_every_binding_row_has_a_midi_placeholder():
    text = body(prefs())
    for kind, x in pc.BINDING_ROWS:
        label = pc.binding_label(x) if kind == "action" else x[1]
        line = next(t for t in text if t.strip().startswith(label) and t.count("—"))
        assert line.rstrip().endswith("—")


def test_action_rows_still_rebind_and_reset_still_asks():
    st, _ = key(prefs(cursor=row_of("action", "patch.grid")), "enter")
    assert st.key_capture == "patch.grid"
    st, actions = key(st, "G")
    assert st.keys == {"patch.grid": "G"} and actions == [("save_ui",)]
    assert pc.RESET_KEYS_ROW == pc.KEYS_ROW + len(pc.BINDING_ROWS)
    assert key(prefs(cursor=pc.RESET_KEYS_ROW), "enter")[0].popup == ("reset_keys",)


# --- Display notes in the status bar ---------------------------------------------------------------

def test_display_notes_move_to_the_status_bar():
    st = prefs(cursor=pc.BAR_ROW)
    out = lines(st, height=40)
    assert not any("the bar under each Mixer channel" in t for t, _ in out[:-1])
    assert "the bar under each Mixer channel" in out[-1][0]
    assert "the key list at the bottom of the screen" in lines(prefs(cursor=pc.KEY_BOX_ROW), 40)[-1][0]


def test_a_message_still_wins_the_status_bar():
    out = lines(prefs(cursor=pc.BAR_ROW, message="saved"), height=40)
    assert "saved" in out[-1][0] and "the bar under" not in out[-1][0]


def test_no_note_off_the_display_rows():
    assert lines(prefs(cursor=pc.RESET_KEYS_ROW), 40)[-1][0].strip() == pc.HINTS.get("settings", "").strip()
