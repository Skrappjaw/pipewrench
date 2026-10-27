"""Themes as TOML files: palette → general → specific (Skrappjaw, 2026-10-26: "general and specific ui elements so a
simple meta file can be edited by user per theme")."""
import json
from pathlib import Path

import pytest

import pwcore as pc
import pwthemes as pt

FIX = Path(__file__).parent / "fixtures"
GOLDEN = json.loads((FIX / "themes_golden.json").read_text())


def tup(theme):
    return {r: list(v) for r, v in theme.items()}


def write(d, name, text):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(text)
    return d


# --- the built-ins look exactly as they did ----------------------------------------------------------

def test_built_ins_match_the_old_dicts_exactly():
    lib = pt.load([pt.BUILTIN_DIR])
    assert lib.problems == []
    assert lib.names == GOLDEN["names"]
    assert {n: tup(t) for n, t in lib.themes.items()} == GOLDEN["themes"]
    assert {n: lib.label(n) for n in lib.names} == GOLDEN["labels"]
    assert {n: lib.min_colours(n) for n in lib.names} == GOLDEN["min_colours"]


def test_pwcore_still_offers_them():
    assert pc.THEME_NAMES == GOLDEN["names"]
    assert {n: tup(t) for n, t in pc.THEMES.items()} == GOLDEN["themes"]
    assert {n: json.loads(json.dumps(pc.logo_chain(n))) for n in pc.THEME_NAMES} == GOLDEN["logos"]


# --- the format -------------------------------------------------------------------------------------

def test_palette_general_and_a_specific_override(tmp_path):
    d = write(tmp_path, "studio", '''
name = "Studio"
extends = "dark"
[palette]
ink = "#101010"
sky = "#88ccff"
[general]
selected = { fg = "ink", bg = "sky" }
[specific]
popup_sel = { fg = "#ffffff", bg = "#ff0000", attr = "bold" }
''')
    lib = pt.load([pt.BUILTIN_DIR, d])
    assert lib.problems == [] and lib.names[-1] == "studio" and lib.label("studio") == "Studio"
    t = lib.themes["studio"]
    assert t["selected"] == ("#101010", "#88ccff", "")
    assert t["bar_src"] == t["selected"]                     # specific derives from general
    assert t["popup_sel"] == ("#ffffff", "#ff0000", "bold")  # an override wins
    assert t["chip"] == lib.themes["dark"]["chip"]           # the rest comes from extends
    assert lib.min_colours("studio") == 256 and lib.user == ["studio"]


def test_a_theme_without_extends_fills_gaps_from_dark(tmp_path):
    d = write(tmp_path, "tiny", '[general]\ntitle = { fg = "magenta", attr = "bold" }\n')
    lib = pt.load([pt.BUILTIN_DIR, d])
    assert lib.themes["tiny"]["title"] == ("magenta", None, "bold")
    assert lib.themes["tiny"]["chip"] == lib.themes["dark"]["chip"] and lib.min_colours("tiny") == 8


def test_extends_a_user_theme_and_inherits_the_logo(tmp_path):
    write(tmp_path, "a", 'extends = "crowbar"\n')
    write(tmp_path, "b", 'extends = "a"\n[general]\nok = { fg = "green" }\n')
    lib = pt.load([pt.BUILTIN_DIR, tmp_path])
    assert lib.themes["b"]["chip"] == lib.themes["crowbar"]["chip"] and lib.logo("b") == lib.logo("crowbar")


@pytest.mark.parametrize("text, says", [
    ('[general]\nchip = { fg = "blu" }\n', 'general.chip.fg "blu" is not a colour'),
    ('[general]\nchipz = { fg = "red" }\n', 'general.chipz is not a general role'),
    ('[specific]\nselected = { fg = "red" }\n', 'specific.selected is not a specific role'),
    ('[general]\nchip = { fg = "red", attr = "blink" }\n', 'general.chip.attr "blink"'),
    ('extends = "nope"\n', 'extends "nope": no such theme'),
    ('logo = "nope"\n', 'logo "nope"'),
    ('[general\n', 'not valid TOML'),
])
def test_a_broken_theme_is_skipped_with_a_clear_problem(tmp_path, text, says):
    lib = pt.load([pt.BUILTIN_DIR, write(tmp_path, "bad", text)])
    assert "bad" not in lib.themes and len(lib.problems) == 1
    assert lib.problems[0].startswith("themes/bad.toml: ") and says in lib.problems[0]


def test_a_cycle_and_a_clash_are_skipped(tmp_path):
    write(tmp_path, "x", 'extends = "y"\n')
    write(tmp_path, "y", 'extends = "x"\n')
    write(tmp_path, "dark", '[general]\nok = { fg = "red" }\n')
    lib = pt.load([pt.BUILTIN_DIR, tmp_path])
    assert "x" not in lib.themes and "y" not in lib.themes
    assert lib.themes["dark"] == pt.load([pt.BUILTIN_DIR]).themes["dark"]
    text = "\n".join(lib.problems)
    assert "extends loops" in text and 'themes/dark.toml: "dark" is a built-in theme' in text


# --- export ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", GOLDEN["names"])
def test_export_loads_back_as_the_same_theme(tmp_path, name):
    lib = pt.load([pt.BUILTIN_DIR])
    text = pt.export(lib, name, "mine")
    assert f'extends = "{name}"' in text and "# popup_sel" in text  # specific roles offered as comments
    back = pt.load([pt.BUILTIN_DIR, write(tmp_path, "mine", text)])
    assert back.problems == [] and back.themes["mine"] == lib.themes[name]


def test_every_role_is_documented():
    assert set(pt.ROLES) == set(pt.GENERAL) | set(pt.SPECIFIC)
    assert all(where and layer in ("general", "specific") for layer, where, _ in pt.ROLES.values())


def test_the_theme_guide_lists_every_role():
    doc = (Path(__file__).parent.parent / "docs" / "themes.md").read_text()
    assert pt.role_table() in doc


# --- command line and the Settings row -----------------------------------------------------------------

def cli(*argv):
    import io
    import pipewrench
    out, err = io.StringIO(), io.StringIO()
    rc = pipewrench.main(list(argv), runner=lambda a: (0, ""), out=out, err=err)
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path / "pipewrench" / "themes"
    pc.install_themes()


def test_cli_lists_exports_and_checks(home):
    rc, out, _ = cli("theme", "list")
    assert rc == 0 and "pipeos-dark" in out and "pipeOS-dark" in out and "built-in" in out
    rc, out, _ = cli("theme", "export", "pipeos-dark")
    path = home / "pipeos-dark-custom.toml"
    assert rc == 0 and path.exists() and str(path) in out
    rc, _, err = cli("theme", "export", "pipeos-dark")
    assert rc != 0 and "already exists" in err                 # never overwrites
    rc, out, _ = cli("theme", "list")
    assert "pipeos-dark-custom" in out and "yours" in out
    rc, out, _ = cli("theme", "check")
    assert rc == 0 and "no problems" in out
    path.write_text(path.read_text().replace('chip = { fg = "#ffffff"', 'chip = { fg = "#fffff"'))
    rc, out, _ = cli("theme", "check")
    assert rc == 1 and "general.chip.fg" in out
    rc, _, err = cli("theme", "export", "nope")
    assert rc == 2 and "no theme" in err


def test_opening_the_theme_row_is_the_reload_moment():
    closed = pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane", cursor=pc.THEME_ROW)
    opened = pc.dataclasses.replace(closed, editing=True)
    assert pc.opened_theme_row(closed, opened) and not pc.opened_theme_row(opened, opened)
    assert not pc.opened_theme_row(closed, pc.dataclasses.replace(opened, cursor=pc.FPS_ROW))
    assert pc.themes_message([]) == (f"{len(pc.THEME_NAMES)} themes", "")
    msg, failed = pc.themes_message(["themes/a.toml: x", "themes/b.toml: y"])
    assert msg == failed and msg.startswith("themes/a.toml: x") and "+1 more" in msg


def test_message_bands_are_bold_and_never_bold_black():
    # Skrappjaw, 2026-10-26: "Make the status bar a bold color highlight that is more obvious" → bold light text
    # where the band's text was black (his 2026-10-24 rule: no bold on black).
    for name, t in pt.load([pt.BUILTIN_DIR]).themes.items():
        for role in ("band_ok", "band_alert"):
            fg, _, attr = t[role]
            assert "bold" in attr.split() and fg not in ("black", "#000000"), (name, role)
    dark = pt.load([pt.BUILTIN_DIR]).themes["dark"]
    assert dark["band_ok"][0] == "white" and dark["status_line"] == dark["anchor"]
