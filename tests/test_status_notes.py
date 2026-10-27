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
        assert text and text != hint("devices") and "Enter" in text, kind


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
    assert "QUANT" in note(pc.UIState(tab="top"))


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
