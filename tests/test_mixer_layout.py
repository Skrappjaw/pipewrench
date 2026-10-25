"""Mixer row (Skrappjaw, 2026-10-24): one line per channel, the volume % written inside the bar and inverted to
blend with it. The bar can be switched off in Settings → Display, leaving just the %, and then the meter
moves in beside it."""
import dataclasses
import json
from pathlib import Path

import pytest

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi

FIX = Path(__file__).parent / "fixtures"
DUMP = json.loads((FIX / "pwdump_patch.json").read_text())
FIRST = "DualShock 4 [CUH-ZCT2x] Analog Stereo"


def mx(volume=None, mute=None, metered=False):
    m = pc.mixer_view(pi.inventory(DUMP), pcfg.default_config(), pi.streams(DUMP))
    devs, apps = m["playback"]
    first = dataclasses.replace(devs[0], **{k: v for k, v in (("volume", volume), ("mute", mute)) if v is not None})
    m["playback"] = ([first] + devs[1:], apps)
    m["metered"] = {("playback", first.id): first.serial} if metered else {}
    m["levels"] = {("playback", first.id): (0.5, None)} if metered else {}
    return m


def screen(m=None, width=100, height=60, **st):
    return pc.render_screen(pc.UIState(tab="mixer", **st), {}, None, [], width, height, mixer=m or mx())


def line(lines, label):
    return next((t, s) for t, s in lines if label in t)


def roles_at(ln, c0, c1):
    _, (base, spans) = ln
    got = []
    for col in range(c0, c1):
        role = base
        for a, b, r in spans:
            if a <= col < b:
                role = r
        got.append(role)
    return got


def vol_col(text):
    return text.index("%") - 3 if "100%" in text else text.index("%") - 2


def test_one_line_per_channel():
    lines = screen()[0]
    rows = [t for t, _ in lines if t.startswith(("> ", "  ★", "    ")) and "%" in t]
    assert len(rows) == len(pc.mixer_selectable(mx()))
    t, _ = line(lines, FIRST)
    assert "100%█" in t


def test_percent_is_inverted_over_filled_cells():
    ln = line(screen()[0], FIRST)
    c = ln[0].index("100%")
    assert roles_at(ln, c, c + 4) == ["bar_ok"] * 4
    assert len(ln[0][c:].split("  ")[0]) == pc._BAR  # the % takes the place of the first cells


def test_percent_over_empty_cells_at_low_volume():
    ln = line(screen(mx(volume=0.1))[0], FIRST)
    c = ln[0].index("10%")
    assert "bar_dim" in roles_at(ln, c, c + 3)


def test_past_100_is_alert():
    ln = line(screen(mx(volume=1.5))[0], FIRST)
    c = ln[0].index("150%")
    assert roles_at(ln, c, c + 4) == ["bar_ok"] * 4 and "alert" in roles_at(ln, c, c + pc._BAR)


def test_muted_word_is_dim():
    ln = line(screen(mx(mute=True))[0], FIRST)
    c = ln[0].index("muted")
    assert set(roles_at(ln, c, c + 5)) == {"bar_dim"}


def test_bar_off_leaves_the_percent_and_the_meter_moves_in():
    on = line(screen(mx(metered=True))[0], FIRST)[0]
    off = line(screen(mx(metered=True), volume_bar=False)[0], FIRST)[0]
    assert "█" in on and "░" not in off
    meter_on, meter_off = on.rindex("  ") + 2, off.rindex("  ") + 2
    assert off.index("100%") + 7 == meter_off       # "100% " then two spaces, then the meter
    assert meter_off < meter_on


def test_bar_off_columns_line_up():
    lines = screen(volume_bar=False)[0]
    cols = {t.index("%") - 3 for t, _ in lines if t.rstrip().endswith("100%")}
    assert len(cols) == 1


def test_bar_off_no_volume_control_reads_n_a():
    assert pc._volume_bar(dataclasses.replace(mx()["playback"][0][0], volume=None), False)[0] == "n/a"


@pytest.mark.parametrize("width", [40, 80, 120])
@pytest.mark.parametrize("bar", [True, False])
def test_fits_width(width, bar):
    assert all(len(t) <= width for t, _ in screen(mx(metered=True), width=width, volume_bar=bar)[0])


def test_every_theme_defines_the_bar_roles():
    for theme in pc.THEMES.values():
        assert {"bar_ok", "bar_alert", "bar_dim"} <= set(theme)


# --- Settings ------------------------------------------------------------------

def test_volume_bar_row_toggles_and_saves():
    st = pc.UIState(tab="settings", cursor=pc.BAR_ROW)
    st, actions = pc.handle_key(st, "right", {}, [])
    assert st.volume_bar is False and actions == [("save_ui",)]
    st, _ = pc.handle_key(st, "left", {}, [])
    assert st.volume_bar is True and pc.ui_prefs(st)["volume_bar"] is True


def test_volume_bar_row_renders():
    lines, _ = pc.render_screen(pc.UIState(tab="settings"), {}, [], [], 100, 50)
    assert any("Volume bar" in t and "on" in t for t, _ in lines)


def test_load_ui_defaults_bar_on(tmp_path):
    assert pc.load_ui(tmp_path / "missing.json")["volume_bar"] is True
    (tmp_path / "ui.json").write_text('{"volume_bar": false}')
    assert pc.load_ui(tmp_path / "ui.json")["volume_bar"] is False


def test_keys_then_presets_follow_the_bar_row():
    assert pc.KEYS_ROW == pc.BAR_ROW + 1 and pc.PRESETS_ROW == pc.RESET_KEYS_ROW + 1
