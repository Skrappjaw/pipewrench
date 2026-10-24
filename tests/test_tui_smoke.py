"""Drive the real curses UI in a pty against recorded fixtures (no PipeWire calls)."""
import fcntl
import json
import os
import pty
import select
import struct
import sys
import termios
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import pwcore as pc  # noqa: E402


def tab(name):
    """The number key for a tab, so these tests follow the tab order."""
    return str(pc.TABS.index(name) + 1)
APP = HERE.parent / "pwctl.py"


class Pty:
    def __init__(self, tmp_path, term="xterm-256color"):
        env = {**os.environ, "TERM": term, "PWCTL_FIXTURES": str(HERE / "fixtures"),
               "PWCTL_PRESETS": str(tmp_path / "presets.json"), "XDG_CONFIG_HOME": str(tmp_path)}
        env.pop("PWCTL_DEVICES", None)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(sys.executable, [sys.executable, "-I", str(APP)], env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        self.buf = b""

    def expect(self, needle, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if needle.encode() in self.buf:
                self.buf = b""
                return True
            r, _, _ = select.select([self.fd], [], [], 0.1)
            if r:
                try:
                    self.buf += os.read(self.fd, 65536)
                except OSError:
                    break
        raise AssertionError(f"never saw {needle!r}; tail: {self.buf[-400:]!r}")

    def send(self, data):
        os.write(self.fd, data.encode())

    def click(self, row, col):
        self.send(f"\x1b[<0;{col + 1};{row + 1}M")
        self.send(f"\x1b[<0;{col + 1};{row + 1}m")

    def wait_exit(self, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
            select.select([self.fd], [], [], 0.1)
            try:
                os.read(self.fd, 65536)
            except OSError:
                pass
        os.kill(self.pid, 9)
        raise AssertionError("app did not exit")


@pytest.fixture
def app(tmp_path):
    p = Pty(tmp_path)
    yield p
    try:
        os.kill(p.pid, 9)
        os.waitpid(p.pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


def test_tab_key_mouse_click_and_quit(app):
    app.expect("Playback")  # pwctl opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.send("\t")
    app.expect("QUANT")
    # the tab bar is " " then " n Title " per tab, one space apart: click inside "Settings"
    col = 1 + sum(len(pc.TAB_TITLES[t]) + 3 for t in pc.TABS[:pc.TABS.index("settings")]) + 3
    app.click(0, col)
    app.expect("Force sample rate")
    app.send("q")
    assert app.wait_exit() == 0


def test_save_preset_via_prompt(app, tmp_path):
    app.expect("Playback")  # pwctl opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.expect("no presets")  # Presets sit inside Settings
    app.send("s")
    app.expect("preset name")
    app.send("tracking\r")
    app.expect("label for tracking")
    app.send("Low latency\r")
    app.expect("saved preset tracking")
    app.send("q")
    assert app.wait_exit() == 0
    assert "Low latency" in (tmp_path / "presets.json").read_text()


def test_devices_tab_lists_inventory_and_adds_a_bus(app, tmp_path):
    app.expect("Playback")  # pwctl opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.send(tab("devices"))
    app.expect("GCS-8 · profile HiFi")
    app.send("a")
    app.expect("bus name")
    app.send("OBS\r")
    app.expect("OBS created")  # curses redraws only what changed; "bus " was already on screen
    app.send("q")
    assert app.wait_exit() == 0
    saved = json.loads((tmp_path / "pwctl" / "devices.json").read_text())
    assert saved["buses"] == [{"name": "OBS", "label": "OBS"}]
    assert 'node.name = "OBS"' in (tmp_path / "pipewire" / "pipewire.conf.d" / "50-pwctl-devices.conf").read_text()
    assert not (tmp_path / "wireplumber").exists()


def test_patch_tab_shows_both_views(app):
    app.expect("Playback")  # pwctl opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.send(tab("patch"))
    app.expect("Destinations")
    app.expect("Discord")
    app.send("g")
    app.expect("column view")
    app.send("p")
    app.expect("Ports:")
    app.send("q")
    assert app.wait_exit() == 0


def test_devices_rename_is_enter_enter_type_enter(app, tmp_path):
    app.expect("Playback")  # pwctl opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.send(tab("devices"))
    app.expect("GCS-8 · profile HiFi")
    app.send("\x1bOB")          # DualShock card -> its output row (off cards sit at the bottom)
    app.send("\r")              # Enter: actions
    app.expect("Rename")
    app.send("\x1bOB\r")        # ↓ Enter: Rename… (Set as system default leads the menu)
    app.expect("new name")
    app.send("Pad\r")           # type, Enter
    app.expect("Restart WirePlumber now")
    app.send("n")
    app.send("q")
    assert app.wait_exit() == 0
    saved = json.loads((tmp_path / "pwctl" / "devices.json").read_text())
    assert list(saved["labels"].values()) == ["Pad"]
    lua = (tmp_path / "wireplumber" / "main.lua.d" / "51-pwctl-labels.lua").read_text()
    assert '"Pad"' in lua


def test_devices_rename_empty_restores_the_default(app, tmp_path):
    ds4 = "alsa_output.usb-Sony_Interactive_Entertainment_Wireless_Controller-00.analog-stereo"
    (tmp_path / "pwctl").mkdir(exist_ok=True)
    (tmp_path / "pwctl" / "devices.json").write_text(json.dumps(
        {"version": 2, "clock_rate": None, "buses": [], "labels": {ds4: "Pad"}, "hidden": []}))
    app.expect("Playback")  # pwctl opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.send(tab("devices"))
    app.expect("GCS-8 · profile HiFi")
    app.send("\x1bOB")
    app.send("\r")
    app.expect("Rename")
    app.send("\x1bOB\r")
    app.expect("empty restores the default")
    app.send("\r")              # empty
    app.expect("Restart WirePlumber now")
    app.send("n")
    app.send("q")
    assert app.wait_exit() == 0
    saved = json.loads((tmp_path / "pwctl" / "devices.json").read_text())
    assert saved["labels"] == {}


@pytest.mark.parametrize("term", ["xterm-256color", "xterm", "vt100"])  # 16+ colours, 8 colours, none
def test_theme_switches_live_and_is_saved(tmp_path, term):
    (tmp_path / "pwctl").mkdir()
    (tmp_path / "pwctl" / "ui.json").write_text('{"theme": "light"}')
    app = Pty(tmp_path, term)
    try:
        app.expect("Playback")
        app.send(tab("settings"))
        app.expect("light")
        app.send("\x1bOB" * pc.THEME_ROW + "\x1bOD")  # down to the theme row, then left: light → dark
        app.expect("theme: dark")
        assert json.loads((tmp_path / "pwctl" / "ui.json").read_text()) == {"theme": "dark", "meter_fps": 10, "meter_fall": 20, "peak_hold": 1.5}
        app.send(tab("patch"))
        app.expect("Destinations")
        app.send("q")
        assert app.wait_exit() == 0
    finally:
        try:
            os.kill(app.pid, 9)
            os.waitpid(app.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass


@pytest.mark.parametrize("term", ["xterm-256color", "xterm"])  # exact or nearest colours; dark fallback
def test_crowbar_theme_starts(tmp_path, term):
    (tmp_path / "pwctl").mkdir()
    (tmp_path / "pwctl" / "ui.json").write_text('{"theme": "orange"}')  # the old name still loads
    app = Pty(tmp_path, term)
    try:
        app.expect("needs 256 colours" if term == "xterm" else "Playback")
        app.send(tab("settings"))
        app.expect("crowbar")
        app.send("q")
        assert app.wait_exit() == 0
    finally:
        try:
            os.kill(app.pid, 9)
            os.waitpid(app.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass


def test_numpad_keys_reach_the_media_bar(app):
    """Keypad mode makes the numpad send \\EOj and friends; they must still act as * / - +."""
    app.expect("Playback")
    app.send("\x1bOj")  # numpad *
    app.expect("no media player found")  # fixtures have no player, so the key got through
    app.send("q")
    assert app.wait_exit() == 0
