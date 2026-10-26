"""The user guide (docs/guide.md) and its pictures stay in step: every picture it shows is one that
tools/screenshots.py draws, and every frame fits the terminal it's drawn for."""
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import screenshots  # noqa: E402

GUIDE = (ROOT / "docs" / "guide.md").read_text() + (ROOT / "docs" / "themes.md").read_text()
SHOTS = dict(screenshots.shots())


def test_every_picture_in_the_guide_is_drawn():
    used = set(re.findall(r"\]\(guide/([\w-]+)\.png\)", GUIDE))
    assert used and used <= set(SHOTS), used - set(SHOTS)


def test_every_drawn_picture_is_used():
    used = set(re.findall(r"\]\(guide/([\w-]+)\.png\)", GUIDE))
    assert set(SHOTS) <= used, set(SHOTS) - used


def test_frames_fit_the_terminal():
    for name, lines in SHOTS.items():
        assert all(len(t) <= screenshots.W for t, _ in lines), name


def test_pictures_are_in_the_repo():
    for name in SHOTS:
        assert (ROOT / "docs" / "guide" / f"{name}.png").is_file(), name


def test_no_personal_names_in_the_pictures():
    for name, lines in SHOTS.items():
        text = "\n".join(t for t, _ in lines).lower()
        assert "cole" not in text and "/home/" not in text, name
