"""The ◀ ▶ beside a Settings value are grey while you pass over the row and bright once Enter opens it (Skrappjaw,
2026-10-26: "grey arrows on menu when navigating and restore on select")."""
from pathlib import Path

import pwcore as pc

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())


def lines(st):
    return pc.render_screen(st, CUR, "", 100, 60)[0]


def arrow_roles(st, label):
    """{◀ role, ▶ role} on the row whose text holds label; None where the row's own style applies."""
    text, style = next((t, s) for t, s in lines(st) if label in t and "◀" in t)
    spans = style[1] if isinstance(style, tuple) else []
    roles = []
    for ch in "◀▶":
        x = text.index(ch)
        roles.append(next((r for c0, c1, r in reversed(spans) if c0 <= x < c1), None))
    return roles


def prefs(**kw):
    return pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane", **kw)


ROW0, ROW1 = pc.audio_index(("setting", 0)), pc.audio_index(("setting", 1))  # Audio's first clock rows


def audio(**kw):
    return pc.UIState(tab="settings", settings_cat="audio", settings_focus="pane", **kw)


def test_a_closed_row_off_the_cursor_has_grey_arrows():
    assert arrow_roles(prefs(cursor=pc.FPS_ROW), "Theme") == ["dim", "dim"]
    assert arrow_roles(audio(cursor=ROW1), pc.SETTINGS[0].label) == ["dim", "dim"]


def test_the_cursor_row_keeps_its_bar_with_grey_arrows():
    assert arrow_roles(prefs(cursor=pc.THEME_ROW), "Theme") == ["dim@selected", "dim@selected"]
    assert arrow_roles(audio(cursor=ROW0), pc.SETTINGS[0].label) == ["dim@selected", "dim@selected"]


def test_an_open_row_has_its_arrows_back():
    for st, label in ((prefs(cursor=pc.THEME_ROW, editing=True), "Theme"), (audio(cursor=ROW0, editing=True), pc.SETTINGS[0].label)):
        assert not any(r and r.startswith("dim") for r in arrow_roles(st, label))  # the row's own anchor colour


def test_every_theme_has_the_grey_arrow_roles():
    for name, t in pc.THEMES.items():
        for bar in ("selected", "anchor"):
            assert t[f"dim@{bar}"] == (t["dim"][0], t[bar][1], t["dim"][2]), name
