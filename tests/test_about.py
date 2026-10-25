"""The About tab: the PipeWrench wordmark (docs/about-art.md) over a short info block. The font follows the
width: ANSI Compact where it fits, Pagga where it's narrower, the letterspaced line below that."""
import pytest

import pwcore as pc
from pwcore import UIState

ABOUT = {"pipewire": "1.0.5", "wireplumber": "0.4.17", "config": "~/.config/pipewrench"}


def screen(width=80, height=30, about=ABOUT):
    return pc.render_screen(UIState(tab="about"), {}, [], [], width, height, about=about)


def body(lines):
    return "\n".join(t for t, _ in lines)


def test_about_is_the_last_tab():
    assert pc.TABS[-1] == "about"
    assert pc.TAB_TITLES["about"] == f"{len(pc.TABS)} About"
    st, _ = pc.handle_key(UIState(), str(len(pc.TABS)), {}, [])
    assert st.tab == "about"


def test_wide_terminal_gets_ansi_compact():
    lines, _ = screen(width=100)
    assert pc.LOGO_ANSI[0] in body(lines)
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
    row = next((t, s) for t, s in lines if pc.LOGO_ANSI[0] in t)
    assert row[0].index(pc.LOGO_ANSI[0]) == (100 - 62) // 2
    assert row[1] == "title"


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
    st, actions = pc.handle_key(UIState(tab="about"), "x", {}, [])
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
