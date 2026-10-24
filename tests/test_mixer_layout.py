"""Mixer layout (Skrappjaw, 2026-10-24): each channel is a name line with its volume and meter as sub lines
under it. The volume % is written inside the bar, inverted to blend with it; the bar can be switched off
in Settings → Display, leaving just the %."""
import dataclasses
import json
from pathlib import Path

import pytest

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi

FIX = Path(__file__).parent / "fixtures"
DUMP = json.loads((FIX / "pwdump_patch.json").read_text())


def mx(volume=None, mute=None, metered=()):
    m = pc.mixer_view(pi.inventory(DUMP), pcfg.default_config(), pi.streams(DUMP))
    if volume is not None or mute is not None:
        devs, apps = m["playback"]
        first = dataclasses.replace(devs[0], **{k: v for k, v in (("volume", volume), ("mute", mute)) if v is not None})
        m["playback"] = ([first] + devs[1:], apps)
    first = m["playback"][0][0]
    m["metered"] = {("playback", first.id): first.serial} if metered else {}
    m["levels"] = {("playback", first.id): (0.5, None)} if metered else {}
    return m


def screen(m=None, width=100, height=60, **st):
    return pc.render_screen(pc.UIState(tab="mixer", **st), {}, None, [], width, height, mixer=m or mx())


def group(lines, label):
    """(name line, the lines under it) for a channel, up to the next name or heading."""
    i = next(n for n, (t, _) in enumerate(lines) if t[4:].startswith(label) or t[6:].startswith(label))
    out = []
    for t, s in lines[i + 1:]:
        if not t.startswith("      ") or not t.strip():
            break
        out.append((t, s))
    return lines[i], out


def roles_at(line, c0, c1):
    _, (base, spans) = line
    got = []
    for col in range(c0, c1):
        role = base
        for a, b, r in spans:
            if a <= col < b:
                role = r
        got.append(role)
    return got


FIRST = "DualShock 4 [CUH-ZCT2x] Analog Stereo"


def test_name_line_has_no_bar():
    name, subs = group(screen()[0], FIRST)
    assert "█" not in name[0] and "%" not in name[0]


def test_volume_line_starts_with_the_percent_inside_the_bar():
    _, subs = group(screen()[0], FIRST)
    text = subs[0][0]
    assert text.startswith("      100%") and "█" in text and "░" in text
    assert len(text.strip()) == pc._BAR  # the % takes the place of the first cells


def test_percent_is_inverted_over_filled_cells():
    _, subs = group(screen()[0], FIRST)
    assert roles_at(subs[0], 6, 10) == ["bar_ok"] * 4


def test_percent_over_empty_cells_at_low_volume():
    _, subs = group(screen(mx(volume=0.1))[0], FIRST)
    assert subs[0][0].startswith("      10%")
    assert "bar_dim" in roles_at(subs[0], 6, 9)


def test_percent_past_100_is_alert_inverted():
    _, subs = group(screen(mx(volume=1.5))[0], FIRST)
    text = subs[0][0]
    assert text.startswith("      150%")
    roles = roles_at(subs[0], 6, 6 + pc._BAR)
    assert roles[:4] == ["bar_ok"] * 4 and "alert" in roles


def test_muted_shows_the_word_dim():
    _, subs = group(screen(mx(mute=True))[0], FIRST)
    assert subs[0][0].startswith("      muted")
    assert set(roles_at(subs[0], 6, 11)) == {"bar_dim"}


def test_bar_off_leaves_just_the_percent():
    _, subs = group(screen(volume_bar=False)[0], FIRST)
    assert subs[0][0].rstrip() == "      100%"


def test_meter_line_only_on_metered_rows():
    _, subs = group(screen(mx(metered=True))[0], FIRST)
    assert len(subs) == 2 and any(ch in subs[1][0] for ch in "▐·")
    _, subs = group(screen(mx(metered=True))[0], "GCS-8 MAIN-AUX")
    assert len(subs) == 1


def test_app_rows_indent_deeper():
    lines = screen()[0]
    i = next(n for n, (t, _) in enumerate(lines) if "Discord" in t and "→" in t)
    assert lines[i + 1][0].startswith("        100%")


def test_click_on_a_sub_line_selects_the_channel():
    lines, hits = screen()
    i = next(n for n, (t, _) in enumerate(lines) if t.rstrip().endswith("GCS-8 MAIN-AUX"))
    targets = {t for row, _, _, t in hits if row == i + 1}
    assert targets == {t for row, _, _, t in hits if row == i}
    assert any(t[0] == "mix_row" for t in targets)


def test_menu_box_sits_after_the_group():
    st, _ = pc.handle_key(pc.UIState(tab="mixer"), "enter", {}, [], mixer=mx())
    lines = pc.render_screen(st, {}, None, [], 100, 60, mixer=mx())[0]
    i = next(n for n, (t, _) in enumerate(lines) if FIRST in t)
    assert lines[i + 1][0].startswith("      100%")
    assert lines[i + 2][0].startswith("    ┌")


def test_cursor_scrolls_so_the_whole_group_shows():
    m = mx()
    last = len(pc.mixer_selectable(m)) - 1
    lines, _ = screen(m, height=16, mix_cursor=last)
    _, sec, row = pc.mixer_selectable(m)[last]
    i = max(n for n, (t, _) in enumerate(lines) if row.label in t)
    assert lines[i + 1][0].startswith("        ") and "%" in lines[i + 1][0]


@pytest.mark.parametrize("width", [40, 80, 120])
def test_fits_width(width):
    assert all(len(t) <= width for t, _ in screen(mx(metered=True), width=width)[0])


def test_every_theme_defines_the_bar_roles():
    for theme in pc.THEMES.values():
        assert {"bar_ok", "bar_alert", "bar_dim"} <= set(theme)


# --- Settings ------------------------------------------------------------------

def test_volume_bar_row_toggles_and_saves():
    st = pc.UIState(tab="settings", cursor=pc.BAR_ROW)
    st, actions = pc.handle_key(st, "right", {}, [])
    assert st.volume_bar is False and actions == [("save_ui",)]
    st, actions = pc.handle_key(st, "left", {}, [])
    assert st.volume_bar is True
    assert pc.ui_prefs(st)["volume_bar"] is True


def test_volume_bar_row_renders():
    lines, _ = pc.render_screen(pc.UIState(tab="settings"), {}, [], [], 100, 50)
    assert any("Volume bar" in t and "on" in t for t, _ in lines)


def test_load_ui_defaults_bar_on(tmp_path):
    assert pc.load_ui(tmp_path / "missing.json")["volume_bar"] is True
    (tmp_path / "ui.json").write_text('{"volume_bar": false}')
    assert pc.load_ui(tmp_path / "ui.json")["volume_bar"] is False


def test_presets_row_follows_the_bar_row():
    assert pc.PRESETS_ROW == pc.BAR_ROW + 1
