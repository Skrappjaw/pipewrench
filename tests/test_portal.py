"""Portal themes (Skrappjaw, 2026-10-24): a light test chamber and a dark Portal 2 theme. On Patch, source dots are
portal blue and destination dots portal orange; every other theme keeps its ok/pending colours for them."""
import json
from pathlib import Path

import pytest

import pwcore as pc
import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
G = pp.graph(json.loads((FIX / "pwdump_patch.json").read_text()))
SRCS = [s for s in G.sources if not s.monitor]


def role_at(style, col):
    """The role drawn at one column: the base role, then each span in order (last wins), as Tui._draw does."""
    base, spans = (style, []) if not isinstance(style, tuple) else style
    role = base
    for c0, c1, r in spans:
        if c0 <= col < c1:
            role = r
    return role


def patch_lines(theme="dark", **kw):
    src = next(i for i, s in enumerate(SRCS) if s.label == "Discord")
    st = pc.UIState(tab="patch", patch_src=src, theme=theme, **kw)
    return pc.render_screen(st, {}, None, [], 100, 40, patch=G)[0]


def test_every_theme_has_the_dot_roles():
    for t in pc.THEMES.values():
        assert {"src", "dst", "dst_partial"} <= set(t)


@pytest.mark.parametrize("name", ["dark", "light", "crowbar", "pipeboy", "vaporware", "pipecraft"])
def test_other_themes_keep_their_dot_colours(name):
    t = pc.THEMES[name]
    assert t["src"] == t["ok"] and t["dst"] == t["ok"] and t["dst_partial"] == t["pending"]


@pytest.mark.parametrize("name", ["portal-light", "portal-dark"])
def test_portal_dots_are_blue_and_orange(name):
    t = pc.THEMES[name]
    r, g, b = pc.hex_rgb(t["src"][0])
    assert b > r and b > 150                      # blue portal
    r, g, b = pc.hex_rgb(t["dst"][0])
    assert r > g > b and r > 200                  # orange portal
    assert t["dst_partial"][0] == t["dst"][0]     # partial is the same orange; the half dot shows it
    assert pc.THEME_MIN_COLOURS[name] == 256


def test_portal_light_is_light_and_portal_dark_is_dark():
    light = sum(pc.hex_rgb(pc.THEMES["portal-light"]["screen"][1]))
    dark = sum(pc.hex_rgb(pc.THEMES["portal-dark"]["screen"][1]))
    assert light > 600 and dark < 120
    assert pc.theme_label("portal-light") == "Portal Light" and pc.theme_label("portal-dark") == "Portal Dark"


def test_patch_dots_use_source_and_destination_roles():
    lines = patch_lines(patch_side="dst")  # cursor on the destinations, so the source row isn't the active bar
    text, style = next((t, s) for t, s in lines if t.lstrip("> *").startswith("Discord") and "│" in t)
    assert role_at(style, text.index("●")) in ("src", "anchor")
    dst_text, dst_style = next((t, s) for t, s in lines if "GCS-8 CH 4-AUX" in t and "│" in t)
    col = dst_text.index("●", dst_text.index("│"))
    assert role_at(dst_style, col) in ("dst", "selected")
    # a destination row that isn't under the cursor shows the dst role itself
    dots = [(t, s) for t, s in lines if "│" in t and "●" in t.split("│")[1]]
    assert any(role_at(s, t.index("●", t.index("│"))) == "dst" for t, s in dots) or len(dots) == 1


def test_source_dot_is_src_off_the_cursor():
    lines = patch_lines()  # cursor on the sources: Discord's bar is "selected", other linked sources show src
    linked = [(t, s) for t, s in lines if "│" in t and "●" in t.split("│")[0] and not t.startswith(("> ", "* "))]
    assert linked and all(role_at(s, t.index("●")) == "src" for t, s in linked)


def test_cursor_bar_still_wins_over_a_dot():
    lines = patch_lines()
    text, style = next((t, s) for t, s in lines if t.startswith("> ") and "│" in t)
    if "●" in text.split("│")[0]:
        assert role_at(style, text.index("●")) == "selected"
