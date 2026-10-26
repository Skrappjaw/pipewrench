"""The About tab: the PipeWrench wordmark over a short info block. The font follows the theme (ANSI Compact,
Doom, Computer or Slant) where it fits, Pagga where it's narrower, the letterspaced line below that."""
import pytest

import pwcore as pc
from pwcore import UIState

ABOUT = {"pipewire": "1.0.5", "wireplumber": "0.4.17", "config": "~/.config/pipewrench"}


def screen(width=80, height=30, about=ABOUT):
    return pc.render_screen(UIState(tab="about"), {}, [], width, height, about=about)


def body(lines):
    return "\n".join(t for t, _ in lines)


def test_about_is_the_last_tab():
    assert pc.TABS[-1] == "about"
    assert pc.TAB_TITLES["about"] == f"{len(pc.TABS)} About"
    st, _ = pc.handle_key(UIState(), str(len(pc.TABS)), {})
    assert st.tab == "about"


def test_wide_terminal_gets_the_wrench():
    lines, _ = screen(width=100)
    assert all(row.rstrip() in body(lines) for row in pc.LOGO_WRENCH) and pc.LOGO_ANSI[0] not in body(lines)
    assert pc.LOGO_PAGGA[0] not in body(lines)


def test_narrow_terminal_gets_pagga():
    lines, _ = screen(width=50)
    assert pc.LOGO_PAGGA[0] in body(lines)
    assert pc.LOGO_ANSI[0] not in body(lines)


def test_very_narrow_terminal_gets_the_letterspaced_line():
    lines, _ = screen(width=40)
    assert "P I P E W R E N C H" in body(lines)
    assert "█" not in body(lines)


def test_logo_rows_are_one_width():
    for logo in (pc.LOGO_ANSI, pc.LOGO_PAGGA):
        assert len({len(row) for row in logo}) == 1
    assert len(pc.LOGO_ANSI[0]) == 62 and len(pc.LOGO_PAGGA[0]) == 40


def test_logo_is_centred_in_the_accent():
    lines, _ = screen(width=100)
    text, (base, spans) = next((t, s) for t, s in lines if pc.LOGO_WRENCH[0] in t)
    assert text.index(pc.LOGO_WRENCH[0]) == (100 - 59) // 2 and base == "title"


def test_wrench_shading_is_dim_and_letters_accent():
    lines, _ = screen(width=100)
    text, (_, spans) = next((t, s) for t, s in lines if pc.LOGO_WRENCH[1] in t)
    dim = {a for a, b, role in spans if role == "dim"}
    assert {text.index("░"), text.index("▒")} <= dim and text.index("█") not in dim


def test_wrench_rows_are_one_width_and_fit_80_columns():
    assert len({len(row) for row in pc.LOGO_WRENCH}) == 1 and len(pc.LOGO_WRENCH[0]) + 4 <= 80


def test_pagga_plate_is_dim_and_letters_accent():
    lines, _ = screen(width=50)
    text, (base, spans) = next((t, s) for t, s in lines if pc.LOGO_PAGGA[0] in t)
    assert base == "title"
    assert spans and all(role == "dim" and set(text[c0:c1]) == {"░"} for c0, c1, role in spans)


def test_info_block():
    text = body(screen()[0])
    assert "terminal control for PipeWire" in text
    assert "PipeWire     1.0.5" in text
    assert "WirePlumber  0.4.17" in text
    assert "~/.config/pipewrench" in text
    assert "github.com/Skrappjaw/pipewrench" in text


def test_info_unknown_while_loading():
    text = body(screen(about=None)[0])
    assert "PipeWire     ?" in text


@pytest.mark.parametrize("width", [30, 40, 50, 66, 80, 120])
def test_about_fits_width(width):
    lines, hits = screen(width=width)
    assert all(len(t) <= width for t, _ in lines)


def test_about_fits_height():
    assert len(screen(height=12)[0]) <= 12


def test_about_legend_is_the_tab_keys():
    assert pc.legend(UIState(tab="about")) == pc.TAB_KEYS


def test_other_keys_do_nothing_on_about():
    st, actions = pc.handle_key(UIState(tab="about"), "x", {})
    assert st.tab == "about" and actions == []


def test_about_info_reads_versions():
    def run(argv):
        if argv == ["pipewire", "--version"]:
            return 0, "pipewire\nCompiled with libpipewire 1.0.5\nLinked with libpipewire 1.0.5\n"
        return 0, "wireplumber\nCompiled with libwireplumber 0.4.17\n"
    info = pc.about_info(run)
    assert info["pipewire"] == "1.0.5" and info["wireplumber"] == "0.4.17"


def test_about_info_survives_missing_tools():
    def run(argv):
        raise OSError("not found")
    info = pc.about_info(run)
    assert info["pipewire"] == "?" and info["wireplumber"] == "?"


# --- a wordmark per theme (Skrappjaw, 2026-10-24): dark/light ANSI Compact, crowbar Doom, Pipe Boy Computer,
#     Vaporware Slant; narrow terminals fall back to Pagga, then the letterspaced line

def themed(theme, width=100):
    return body(pc.render_screen(UIState(tab="about", theme=theme), {}, [], width, 30, about=ABOUT)[0])


@pytest.mark.parametrize("theme,logo", [("dark", "LOGO_WRENCH"), ("light", "LOGO_WRENCH"), ("pipeos-dark", "LOGO_ANSI"), ("crowbar", "LOGO_DOOM"),
                                        ("pipeboy", "LOGO_COMPUTER"), ("pipeboy-amber", "LOGO_COMPUTER"),
                                        ("pipeboy-cyan", "LOGO_COMPUTER"), ("pipeboy-white", "LOGO_COMPUTER"),
                                        ("vaporware", "LOGO_SLANT")])
def test_each_theme_has_its_wordmark(theme, logo):
    text = themed(theme)
    assert all(row.rstrip() in text for row in getattr(pc, logo))
    others = {"LOGO_WRENCH", "LOGO_ANSI", "LOGO_DOOM", "LOGO_COMPUTER", "LOGO_SLANT"} - {logo}
    assert not any(getattr(pc, o)[1].rstrip() in text for o in others)


def test_every_theme_has_a_logo_entry():
    assert all(pc.THEME_LOGOS[t] in pc.pt.LOGOS and pc.logo_chain(t) for t in pc.THEME_NAMES)


@pytest.mark.parametrize("theme", ["dark", "light", "crowbar", "pipeboy", "vaporware"])
def test_themed_logos_fall_back_when_narrow(theme):
    assert pc.LOGO_PAGGA[0] in themed(theme, width=50)
    assert "P I P E W R E N C H" in themed(theme, width=40)


def test_themed_logos_fit_80_columns_and_stay_aligned():
    for logo in (pc.LOGO_DOOM, pc.LOGO_COMPUTER, pc.LOGO_SLANT, pc.LOGO_PIPECRAFT):
        assert len({len(row) for row in logo}) == 1 and len(logo[0]) + 4 <= 80


def test_pipecraft_says_pipecraft_at_every_width():
    assert all(row.rstrip() in themed("pipecraft") for row in pc.LOGO_PIPECRAFT)
    assert pc.LOGO_PIPECRAFT_SMALL[0] in themed("pipecraft", width=50)
    assert "P I P E C R A F T" in themed("pipecraft", width=34)
    assert "PIPEWRENCH" not in themed("pipecraft", width=50) and pc.LOGO_PAGGA[0] not in themed("pipecraft", width=50)
