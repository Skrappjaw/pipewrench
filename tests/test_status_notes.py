"""Every row explains itself on the status line (Skrappjaw, 2026-10-26: "Everything gets an explainer and a defined set
of actions"; punch list: "If each row can be navigated then the status line should give feedback on what that row
is or that there is an action that can be taken"). Plan: notes/status-keys-plan.md."""
import pytest

import pwcore as pc

from test_legends import CUR, DEV, PATCH
from test_mixer import MX


def note(st, width=100):
    lines, _ = pc.render_screen(st, CUR, [], width, 40, devices=DEV, patch=PATCH, mixer=MX)
    return lines[-1][0].strip()


def hint(tab):
    return pc.HINTS.get(tab, "").strip()


def test_every_mixer_row_has_its_own_note_naming_enter():
    for n, (kind, _, row) in enumerate(pc.mixer_selectable(MX)):
        text = note(pc.UIState(tab="mixer", mix_cursor=n))
        assert text and text != hint("mixer"), row.label
        if row.volume is not None:
            assert "←→ volume" in text and "Enter: " in text, text


def test_every_devices_row_has_its_own_note():
    for n, (kind, _) in enumerate(pc.dev_selectable(DEV, pc.UIState(tab="devices"))):
        text = note(pc.UIState(tab="devices", dev_cursor=n))
        assert text and text != hint("devices") and ("Enter" in text or kind == "chan"), kind  # a channel only shows


def test_menu_items_explain_themselves():
    st, _ = pc.handle_key(pc.UIState(tab="mixer"), "enter", CUR, mixer=MX)
    assert st.mix_menu and note(st)


@pytest.mark.parametrize("kw", [{}, {"patch_side": "dst"}, {"patch_view": "grid"},
                                {"patch_ports": True, "patch_side": "dst"}])
def test_every_patch_selection_has_a_note(kw):
    text = note(pc.UIState(tab="patch", **kw))
    assert text and text != hint("patch") and "Enter" in text


def test_settings_menu_pane_and_top_have_notes():
    assert "Enter to open" in note(pc.UIState(tab="settings"))
    for cursor in range(len(pc.audio_rows())):
        assert note(pc.UIState(tab="settings", settings_focus="pane", cursor=cursor)), cursor
    for cursor in range(pc.RESET_KEYS_ROW + 1):
        assert note(pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane", cursor=cursor)), cursor
    from test_page_boxes import top_block
    lines, _ = pc.render_screen(pc.UIState(tab="top"), CUR, top_block(), 100, 40)
    assert "driver" in lines[-1][0]                      # Top's rows explain themselves (QA pass)


@pytest.mark.parametrize("tab", ["mixer", "devices", "patch", "settings", "top"])
def test_notes_fit_the_status_line(tab):
    text = note(pc.UIState(tab=tab), width=60)
    assert len(text) <= 60


@pytest.mark.parametrize("k", ["left", "right", " "])
def test_volume_keys_on_devices_leave_the_status_line_alone(k):
    # Skrappjaw, 2026-10-26: "status lines are broken if you attempt to change volume on the device screen". The
    # pointer message ("volume and mute are on the Mixer") stuck over every row's note; the keys now do nothing.
    st = pc.UIState(tab="devices", dev_cursor=0)
    after, actions = pc.handle_key(st, k, CUR, DEV)
    assert after.message == "" and actions == [] and note(after) == note(st)


# --- a message clears on the next move (Skrappjaw, 2026-10-26: approved the flagged pointers "as long as none of
# these break the status line when navigating"; "Clear on the next move") ----------------------------------

def handle(st, k):
    return pc.handle_key(st, k, CUR, DEV, PATCH, MX)[0]


def test_the_d_pointer_clears_when_you_move_on_devices():
    st = handle(pc.UIState(tab="devices"), "d")
    assert "Mixer" in st.message
    st = handle(st, "down")
    assert st.message == "" and note(st).startswith(("an ", "a ", "sound card"))  # the row's own note is back


@pytest.mark.parametrize("tab,k", [("mixer", "down"), ("settings", "down"), ("patch", "up"), ("devices", "up"),
                                   ("top", "pgdn"), ("mixer", "tab")])
def test_any_message_clears_on_a_navigation_key(tab, k):
    st = pc.UIState(tab=tab, settings_focus="pane", message="applied")
    assert handle(st, k).message == ""


def test_a_message_the_key_itself_sets_still_shows():
    st = pc.UIState(tab="settings", settings_focus="pane", cursor=pc.audio_index(("setting", 0)),
                    pending={"clock.force-quantum": 256})
    after = handle(st, "left")                 # ← leaves the pane and drops the pending change
    assert after.message == "unapplied changes dropped"


def test_other_keys_keep_the_message():
    assert handle(pc.UIState(tab="mixer", message="applied"), "v").message.startswith("meters")
    assert handle(pc.UIState(tab="devices", message="applied"), "enter").message == "applied"


def test_peak_hold_note_explains_the_setting_and_nothing_else():
    # Skrappjaw, 2026-10-26: "correct peak status line to reflect variable settings" → "Just explain the setting."
    text = note(pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane", cursor=pc.HOLD_ROW))
    assert "peak" in text and "dB/s" not in text


def test_the_default_item_says_it_is_already_the_default():
    items = pc.mixer_selectable(MX)
    n = next(i for i, (k, sec, r) in enumerate(items) if k == "dev" and pc._is_default(MX, sec, r))
    st, _ = pc.handle_key(pc.UIState(tab="mixer", mix_cursor=n), "enter", CUR, mixer=MX)
    assert "already the system default" in note(st)
