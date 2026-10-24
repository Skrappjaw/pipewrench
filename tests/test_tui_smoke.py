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
APP = HERE.parent / "pwctl.py"


class Pty:
    def __init__(self, tmp_path):
        env = {**os.environ, "TERM": "xterm-256color", "PWCTL_FIXTURES": str(HERE / "fixtures"),
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
    app.expect("Force sample rate")
    app.send("\t")
    app.expect("QUANT")
    # tab bar on top tab: " 1 Settings [2 Top] ..." -> click "Settings" at row 0, col 3
    app.click(0, 3)
    app.expect("Force sample rate")
    app.send("q")
    assert app.wait_exit() == 0


def test_save_preset_via_prompt(app, tmp_path):
    app.expect("Force sample rate")
    app.send("3")
    app.expect("no presets")
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
    app.expect("Force sample rate")
    app.send("4")
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
    app.expect("Force sample rate")
    app.send("5")
    app.expect("Destinations")
    app.expect("Discord")
    app.send("g")
    app.expect("column view")
    app.send("p")
    app.expect("Ports:")
    app.send("q")
    assert app.wait_exit() == 0


def test_devices_rename_is_enter_enter_type_enter(app, tmp_path):
    app.expect("Force sample rate")
    app.send("4")
    app.expect("GCS-8 · profile HiFi")
    app.send("\x1bOB")          # Built-in Audio -> DualShock card
    app.send("\x1bOB")          # -> DualShock output row
    app.send("\r")              # Enter: actions
    app.expect("Rename")
    app.send("\r")              # Enter: Rename…
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
