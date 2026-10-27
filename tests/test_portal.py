"""pipeOS themes (Skrappjaw, 2026-10-24; Portal): pipeOS-lite, a clean test chamber, and pipeOS-dark, Portal 2. On Patch, source dots are
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
    return pc.render_screen(st, {}, None, 100, 40, patch=G)[0]


def test_every_theme_has_the_dot_roles():
    for t in pc.THEMES.values():
        assert {"src", "dst", "dst_partial"} <= set(t)


@pytest.mark.parametrize("name", ["dark", "light", "crowbar", "pipeboy", "vaporware", "pipecraft"])
def test_other_themes_keep_their_dot_colours(name):
    t = pc.THEMES[name]
    assert t["src"] == t["ok"] and t["dst"] == t["ok"] and t["dst_partial"] == t["pending"]


@pytest.mark.parametrize("name", ["pipeos-lite", "pipeos-dark"])
def test_portal_dots_are_blue_and_orange(name):
    t = pc.THEMES[name]
    r, g, b = pc.hex_rgb(t["src"][0])
    assert b > r and b > 150                      # blue portal
    r, g, b = pc.hex_rgb(t["dst"][0])
    assert r > g > b and r > 200                  # orange portal
    assert t["dst_partial"][0] == t["dst"][0]     # partial is the same orange; the half dot shows it
    assert pc.THEME_MIN_COLOURS[name] == 256


def test_portal_light_is_light_and_portal_dark_is_dark():
    light = sum(pc.hex_rgb(pc.THEMES["pipeos-lite"]["screen"][1]))
    dark = sum(pc.hex_rgb(pc.THEMES["pipeos-dark"]["screen"][1]))
    assert light > 600 and dark < 120
    assert pc.theme_label("pipeos-lite") == "pipeOS-lite" and pc.theme_label("pipeos-dark") == "pipeOS-dark"


def test_patch_dots_use_source_and_destination_roles():
    lines = patch_lines(patch_side="dst")  # cursor on the destinations, so the source row isn't the active bar
    text, style = next((t, s) for t, s in lines if t.lstrip("> *").startswith("Discord") and "│" in t)
    assert role_at(style, text.index("●")) == "src@anchor_src"
    dst_text, dst_style = next((t, s) for t, s in lines if "GCS-8 CH 4-AUX" in t and "│" in t)
    col = dst_text.index("●", dst_text.index("│"))
    assert role_at(dst_style, col) in ("dst", "dst@bar_dst")
    # a destination row that isn't under the cursor shows the dst role itself
    dots = [(t, s) for t, s in lines if "│" in t and "●" in t.split("│")[1]]
    assert any(role_at(s, t.index("●", t.index("│"))) == "dst" for t, s in dots) or len(dots) == 1


def test_source_dot_is_src_off_the_cursor():
    lines = patch_lines()  # cursor on the sources: Discord's bar is "selected", other linked sources show src
    linked = [(t, s) for t, s in lines if "│" in t and "●" in t.split("│")[0] and not t.startswith(("> ", "* "))]
    assert linked and all(role_at(s, t.index("●")) == "src" for t, s in linked)


def test_dot_under_a_bar_keeps_its_colour():
    # Skrappjaw: the dots were white when highlighted; they keep blue/orange on the bar's background
    lines = patch_lines()
    text, style = next((t, s) for t, s in lines if t.startswith("> ") and "│" in t)
    assert "●" in text.split("│")[0]
    assert role_at(style, text.index("●")) == "src@bar_src"
    assert role_at(style, 0) == "bar_src"
    lines = patch_lines(patch_side="dst")
    text, style = next((t, s) for t, s in lines if "│" in t and "> " in t.split("│")[1])
    right = text.index("│")
    assert role_at(style, right + 3) == "bar_dst"
    if "●" in text[right:]:
        assert role_at(style, text.index("●", right)) == "dst@bar_dst"
    src_row = next((t, s) for t, s in lines if t.startswith("* "))
    assert role_at(src_row[1], 0) == "anchor_src"


@pytest.mark.parametrize("name", ["pipeos-lite", "pipeos-dark"])
def test_pipeos_bars_are_blue_and_orange_and_dots_keep_their_colour(name):
    t = pc.THEMES[name]
    r, g, b = pc.hex_rgb(t["bar_src"][1])
    assert b > r and b > g                         # a blue bar on the sources
    r, g, b = pc.hex_rgb(t["bar_dst"][1])
    assert r > g > b                               # an orange bar on the destinations
    for dot, bar in (("src", "bar_src"), ("src", "anchor_src"), ("dst", "bar_dst"), ("dst", "anchor_dst"),
                     ("dst_partial", "bar_dst"), ("dst_partial", "anchor_dst")):
        assert t[f"{dot}@{bar}"][:2] == (t[dot][0], t[bar][1])   # the dot's colour on the bar's background


@pytest.mark.parametrize("name", ["dark", "crowbar", "pipecraft"])
def test_other_themes_bars_and_dots_look_as_before(name):
    t = pc.THEMES[name]
    assert t["bar_src"] == t["bar_dst"] == t["selected"] and t["anchor_src"] == t["anchor_dst"] == t["anchor"]
    assert t["src@bar_src"] == t["selected"] and t["dst@anchor_dst"] == t["anchor"]
    assert t["head_play"] == t["head_rec"] == t["title"]


@pytest.mark.parametrize("name", ["pipeos-lite", "pipeos-dark"])
def test_pipeos_mixer_headings_blue_playback_orange_recording(name):
    t = pc.THEMES[name]
    assert t["head_play"][0] == t["src"][0] and t["head_rec"][0] == t["dst"][0]


def test_mixer_headings_use_their_roles():
    import json as _json
    import pwinventory as pi
    inv = pi.inventory(_json.loads((FIX / "pwdump_inventory.json").read_text()))
    mx = pc.mixer_view(inv, {"version": 2, "clock_rate": None, "buses": [], "labels": {}, "hidden": []}, [])
    lines = pc.render_screen(pc.UIState(tab="mixer"), {}, None, 100, 60, mixer=mx)[0]
    for title, role in (("Playback", "head_play"), ("Recording", "head_rec")):  # box titles since the QA pass
        text, style = next((t, s) for t, s in lines if t.startswith(f"┌─ {title} "))
        assert role_at(style, 3) == role


def test_placeholder_names_still_load(tmp_path):
    p = tmp_path / "ui.json"
    for old, new in (("portal-light", "pipeos-lite"), ("portal-dark", "pipeos-dark")):
        p.write_text(f'{{"theme": "{old}"}}')
        assert pc.load_ui(p)["theme"] == new


@pytest.mark.parametrize("view", [dict(), dict(patch_view="grid"), dict(patch_ports=True)])
def test_no_connection_dot_is_drawn_in_ok_green(view):
    lines = patch_lines(theme="pipeos-dark", **view)
    for text, style in lines[2:-6]:  # the body; the key box and status line are left out
        for n, ch in enumerate(text):
            if ch in "●◐":
                role = role_at(style, n)
                assert role.split("@")[0] in ("src", "dst", "dst_partial"), (text, role)


# --- headers, volume and level bars (Skrappjaw, 2026-10-24): no green in pipeOS -----------------------

NEW_ROLES = {"head_src": "title", "head_dst": "title", "vol": "ok", "vol_over": "alert",
             "meter_low": "ok", "meter_mid": "pending", "meter_high": "alert"}


@pytest.mark.parametrize("name", ["dark", "light", "crowbar", "pipeboy", "vaporware", "pipecraft"])
def test_other_themes_keep_their_header_and_bar_colours(name):
    t = pc.THEMES[name]
    assert all(t[new] == t[old] for new, old in NEW_ROLES.items())


@pytest.mark.parametrize("name", ["pipeos-lite", "pipeos-dark"])
def test_pipeos_headers_and_bars(name):
    t = pc.THEMES[name]
    assert t["head_src"][0] == t["src"][0] and t["head_dst"][0] == t["dst"][0]
    assert (t["meter_low"][0], t["meter_mid"][0]) == (t["src"][0], t["dst"][0])   # blue, then orange
    for role in ("vol", "meter_low", "meter_mid"):                                  # nothing green
        r, g, b = pc.hex_rgb(t[role][0])
        assert not (g > r and g > b), (role, t[role])
    assert t["bar_ok"][1] == t["vol"][0] and t["bar_alert"][1] == t["vol_over"][0]  # the % blends with the fill


def test_patch_headers_use_their_roles():
    lines = patch_lines(theme="pipeos-dark")
    text, style = next((t, s) for t, s in lines if "Sources" in t and "Destinations" in t)
    assert role_at(style, text.index("Sources")) == "head_src"
    assert role_at(style, text.index("Destinations")) == "head_dst"


def test_volume_and_meters_use_their_roles():
    row = type("R", (), {"volume": 1.2, "mute": False})()
    _, spans = pc._volume_bar(row)
    roles = {r for *_, r in spans}
    assert "vol" in roles and "vol_over" in roles and "ok" not in roles
    _, spans = pc._meter_cells(1.0)
    assert {r for *_, r in spans} >= {"meter_low", "meter_mid", "meter_high"}


# --- accents everywhere (Skrappjaw, 2026-10-25): direction picks blue or orange; chrome is blue; status dots neutral ---

import pwconfig as pcfg  # noqa: E402
import pwinventory as pi  # noqa: E402

INV = pi.inventory(json.loads((FIX / "pwdump_inventory.json").read_text()))
CHROME = ("selected", "anchor", "title", "chip")


def blue(c):
    r, g, b = pc.hex_rgb(c)
    return b > r and b > g


@pytest.mark.parametrize("name", ["pipeos-lite", "pipeos-dark"])
def test_pipeos_chrome_is_blue(name):
    t = pc.THEMES[name]
    assert t["selected"] == t["bar_src"] and t["anchor"] == t["anchor_src"]  # one blue cursor bar everywhere
    assert t["title"][0] == t["src"][0] and blue(t["chip"][1])


@pytest.mark.parametrize("name", ["pipeos-lite", "pipeos-dark"])
def test_pipeos_ok_is_not_green(name):
    r, g, b = pc.hex_rgb(pc.THEMES[name]["ok"][0])
    assert not (g > r and g > b)


def mixer_lines(theme, section, **kw):
    mx = pc.mixer_view(INV, pcfg.default_config(), [])
    rows = pc.mixer_selectable(mx)
    cursor = next(n for n, line in enumerate(rows) if line[1] == section)
    st = pc.UIState(tab="mixer", theme=theme, mix_cursor=cursor, **kw)
    if kw.get("mix_menu") is True:
        st.mix_menu = (section, rows[cursor][2].id)
    return pc.render_screen(st, {}, None, 100, 60, mixer=mx)[0]


@pytest.mark.parametrize("section, bar, anchor, edge", [("playback", "bar_src", "anchor_src", "head_play"),
                                                         ("recording", "bar_dst", "anchor_dst", "head_rec")])
def test_mixer_cursor_and_menu_follow_the_direction(section, bar, anchor, edge):
    lines = mixer_lines("pipeos-dark", section)
    text, style = next((t, s) for t, s in lines if t.startswith("│ > "))  # rows sit inside their box (+2)
    assert role_at(style, 4) == bar
    lines = mixer_lines("pipeos-dark", section, mix_menu=True)
    assert role_at(next(s for t, s in lines if t.startswith("│ > ")), 4) == anchor
    item = next((t, s) for t, s in lines if t.startswith("│     │ > "))
    assert role_at(item[1], 8) == bar
    top = next((t, s) for t, s in lines if t.startswith("│     ┌"))
    assert role_at(top[1], 7) == edge
    right = 2 + len(item[0][2:-2].rstrip()) - 1
    assert role_at(item[1], 6) == edge and role_at(item[1], right) == edge  # its sides too


def devices_lines(theme, pick, **kw):
    dv = pc.devices_view(INV, pcfg.default_config())
    items = pc.dev_selectable(dv, pc.UIState(tab="devices"))
    cursor = next(n for n, (k, o) in enumerate(items) if pick(k, o))
    st = pc.UIState(tab="devices", theme=theme, dev_cursor=cursor, **kw)
    return pc.render_screen(st, {}, None, 120, 60, devices=dv)[0], items[cursor]


@pytest.mark.parametrize("direction, role", [("out", "dir_out"), ("in", "dir_in")])
def test_devices_channel_rows_tint_their_direction(direction, role):
    lines, _ = devices_lines("pipeos-dark", lambda k, o: k == "card")
    text, style = next((t, s) for t, s in lines if t[6:].startswith(direction + " "))  # inside the card's box
    assert role_at(style, 6) == role and role_at(style, 22) == "normal"  # a row now, not dim (QA pass)
    want = "src" if direction == "out" else "dst"
    assert pc.THEMES["pipeos-dark"][role][0] == pc.THEMES["pipeos-dark"][want][0]


@pytest.mark.parametrize("name", ["dark", "light", "crowbar", "pipeboy", "vaporware", "pipecraft"])
def test_other_themes_keep_channel_rows_dim(name):
    t = pc.THEMES[name]
    assert t["dir_out"] == t["dir_in"] == t["dim"]


def test_devices_card_bar_and_menu_are_neutral_blue_with_plain_profile_dots():
    lines, (_, card) = devices_lines("pipeos-dark", lambda k, o: k == "card" and o.profiles,
                                     dev_menu=None)
    assert role_at(next(s for t, s in lines if t.startswith("│ > ")), 4) == "selected"
    lines, _ = devices_lines("pipeos-dark", lambda k, o: k == "card" and o.profiles, dev_menu=("card", card.id))
    for text, style in lines:
        for n, ch in enumerate(text):
            if ch in "●○" and text.lstrip().startswith("│"):
                assert role_at(style, n).split("@")[0] not in ("src", "dst", "dst_partial"), text


def test_patch_slot_menu_edges_are_orange():
    from test_patch_ui import GR, reaper_menu  # REAPER's unnamed inputs open the slot menu
    st, _ = reaper_menu()
    st.theme = "pipeos-dark"
    lines = pc.render_screen(st, {}, None, 100, 40, patch=GR)[0]
    text, style = next((t, s) for t, s in lines if "┌─" in t)
    assert role_at(style, text.index("┌")) == "head_dst"


def test_realtime_dots_show_status_not_portal_colours():
    import pwrealtime as prt
    status = prt.Status(user="u", groups={"pipewire": 95}, member=True)  # a member, PipeWire not found: ● and ◐
    lines = pc.render_screen(pc.UIState(tab="settings", theme="pipeos-dark"), {}, None, 100, 50,
                             realtime=status)[0]
    seen = set()
    for text, style in lines:
        for n, ch in enumerate(text):
            if ch in "●◐":
                seen.add(role_at(style, n))
    assert seen and seen <= {"ok", "pending", "alert"}, seen


@pytest.mark.parametrize("name", ["dark", "light", "crowbar", "pipeboy", "vaporware", "pipecraft"])
def test_other_themes_direction_bars_look_like_the_cursor(name):
    t = pc.THEMES[name]
    assert t["bar_src"] == t["bar_dst"] == t["selected"] and t["anchor_src"] == t["anchor_dst"] == t["anchor"]
    assert t["head_play"] == t["head_rec"] == t["title"]
