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
APP = HERE.parent / "pipewrench.py"


class Pty:
    def __init__(self, tmp_path, term="xterm-256color"):
        env = {**os.environ, "TERM": term, "PIPEWRENCH_FIXTURES": str(HERE / "fixtures"),
               "XDG_CONFIG_HOME": str(tmp_path)}
        env.pop("PIPEWRENCH_DEVICES", None)
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

    def expect_all(self, *needles, timeout=5.0):
        """Every needle, in any order: curses may paint a screen's lines out of order (Patch's side-by-side boxes
        can draw a row before the title line above it), and expect() drops what came before its match."""
        end = time.time() + timeout
        while time.time() < end:
            if all(n.encode() in self.buf for n in needles):
                self.buf = b""
                return True
            r, _, _ = select.select([self.fd], [], [], 0.1)
            if r:
                try:
                    self.buf += os.read(self.fd, 65536)
                except OSError:
                    break
        raise AssertionError(f"never saw all of {needles!r}; tail: {self.buf[-400:]!r}")

    def send(self, data):
        os.write(self.fd, data.encode())

    def click(self, row, col):
        self.send(f"\x1b[<0;{col + 1};{row + 1}M")
        self.send(f"\x1b[<0;{col + 1};{row + 1}m")

    def quit(self):
        """q asks first (Skrappjaw, 2026-10-26); ← onto Quit, Enter."""
        self.send("q")
        self.expect("Quit PipeWrench")
        self.send("\x1bOD\r")

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
    app.expect("Playback")  # PipeWrench opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.send("\t")
    app.expect("QUANT")
    # the tab bar is " " then " n Title " per tab, one space apart: click inside "Settings"
    col = 1 + sum(len(pc.TAB_TITLES[t]) + 3 for t in pc.TABS[:pc.TABS.index("settings")]) + 3
    app.click(0, col)
    app.expect("Force sample rate")
    app.quit()
    assert app.wait_exit() == 0


def test_save_keeps_the_clock_for_the_next_pipewire_start(app, tmp_path):
    app.expect("Playback")  # PipeWrench opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    # No s key any more (Skrappjaw, 2026-10-26): into the pane, down to the buttons, → to Save, Enter.
    app.send("\x1bOC" + "\x1bOB" * pc.audio_index(("buttons",)) + "\x1bOC" * pc.CLOCK_BUTTONS.index("Save") + "\r")
    app.expect("next time PipeWire starts")
    app.quit()
    assert app.wait_exit() == 0
    saved = json.loads((tmp_path / "pipewrench" / "devices.json").read_text())
    assert saved["clock_rate"] == 48000 and saved["clock_quantum"] == 128
    dropin = (tmp_path / "pipewire" / "pipewire.conf.d" / "50-pipewrench-devices.conf").read_text()
    assert "default.clock.rate = 48000" in dropin and "default.clock.quantum = 128" in dropin
    assert not (tmp_path / "wireplumber").exists()  # no labels, so no label rules


def test_devices_tab_lists_inventory_and_adds_a_bus(app, tmp_path):
    app.expect("Playback")  # PipeWrench opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.send(tab("devices"))
    app.expect("Profile")  # a box per card since the QA pass
    app.send("a")
    app.expect("bus name")
    app.send("OBS\r")
    app.expect("OBS created")  # curses redraws only what changed; "bus " was already on screen
    app.quit()
    assert app.wait_exit() == 0
    saved = json.loads((tmp_path / "pipewrench" / "devices.json").read_text())
    assert saved["buses"] == [{"name": "OBS", "label": "OBS"}]
    assert 'node.name = "OBS"' in (tmp_path / "pipewire" / "pipewire.conf.d" / "50-pipewrench-devices.conf").read_text()
    assert not (tmp_path / "wireplumber").exists()


def test_patch_tab_shows_both_views(app):
    app.expect("Playback")  # PipeWrench opens on the Mixer
    app.send(tab("settings"))
    app.expect("Force sample rate")
    app.send(tab("patch"))
    app.expect_all("Destinations", "Discord")
    app.send("g")
    app.expect("column view")
    app.send("p")
    app.expect("back")  # the port matrix's key box: p Esc back
    app.quit()
    assert app.wait_exit() == 0


def test_mixer_enter_opens_a_menu_and_card_profile_lands_on_devices(app):
    app.expect("Playback")      # PipeWrench opens on the Mixer, cursor on the first output
    app.send("\r")              # Enter: actions, not the default
    app.expect("Card profile")
    app.send("\x1bOB" * 4 + "\r")  # default, Mute, Test sound, Rename…, then Card profile…
    app.expect("Hide card")     # Devices, with that card's menu open
    app.quit()
    assert app.wait_exit() == 0


def test_mixer_rename_is_enter_down_enter_type_enter(app, tmp_path):
    app.expect("Playback")      # PipeWrench opens on the Mixer, cursor on the DualShock output
    app.send("\r")              # Enter: actions
    app.expect("Rename")
    app.send("\x1bOB" * 3 + "\r")  # default, Mute, Test sound, then Rename…
    app.expect("new name")
    app.send("Pad\r")           # type, Enter
    app.expect("Restart WirePlumber now")
    app.send("\x1b")           # Esc closes the popover
    app.expect("cancelled")
    app.quit()
    assert app.wait_exit() == 0
    saved = json.loads((tmp_path / "pipewrench" / "devices.json").read_text())
    assert list(saved["labels"].values()) == ["Pad"]
    lua = (tmp_path / "wireplumber" / "main.lua.d" / "51-pipewrench-labels.lua").read_text()
    assert '"Pad"' in lua


def test_mixer_rename_empty_restores_the_default(app, tmp_path):
    ds4 = "alsa_output.usb-Sony_Interactive_Entertainment_Wireless_Controller-00.analog-stereo"
    (tmp_path / "pipewrench").mkdir(exist_ok=True)
    (tmp_path / "pipewrench" / "devices.json").write_text(json.dumps(
        {"version": 2, "clock_rate": None, "buses": [], "labels": {ds4: "Pad"}, "hidden": []}))
    app.expect("Playback")  # PipeWrench opens on the Mixer, cursor on the DualShock output ("Pad")
    app.send("\r")
    app.expect("Rename")
    app.send("\x1bOB" * 3 + "\r")
    app.expect("empty restores the default")
    app.send("\r")              # empty
    app.expect("Restart WirePlumber now")
    app.send("\x1b")           # Esc closes the popover
    app.expect("cancelled")
    app.quit()
    assert app.wait_exit() == 0
    saved = json.loads((tmp_path / "pipewrench" / "devices.json").read_text())
    assert saved["labels"] == {}


@pytest.mark.parametrize("term", ["xterm-256color", "xterm", "vt100"])  # 16+ colours, 8 colours, none
def test_theme_switches_live_and_is_saved(tmp_path, term):
    (tmp_path / "pipewrench").mkdir()
    (tmp_path / "pipewrench" / "ui.json").write_text('{"theme": "light"}')
    app = Pty(tmp_path, term)
    try:
        app.expect("Playback")
        app.send(tab("settings"))
        app.send("\x1bOB" * 2)  # past Network to Preferences
        app.expect("light")
        app.send("\r" + "\x1bOB" * pc.THEME_ROW + "\r\x1bOD")  # into it, the theme row, open it, left: light → dark
        app.expect("theme: dark")
        app.send("\r")  # Enter keeps it; leaving mid-edit would put light back
        assert json.loads((tmp_path / "pipewrench" / "ui.json").read_text()) == {"theme": "dark", "meter_fps": 10, "meter_fall": 20, "peak_hold": 1.5, "volume_bar": True, "meters": True, "key_box": True, "dev_show_hidden": False, "dev_monitors": False, "keys": {}}
        app.send(tab("patch"))
        app.expect("Destinations")
        app.quit()
        assert app.wait_exit() == 0
    finally:
        try:
            os.kill(app.pid, 9)
            os.waitpid(app.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass


@pytest.mark.parametrize("term", ["xterm-256color", "xterm"])  # exact or nearest colours; dark fallback
def test_crowbar_theme_starts(tmp_path, term):
    (tmp_path / "pipewrench").mkdir()
    (tmp_path / "pipewrench" / "ui.json").write_text('{"theme": "orange"}')  # the old name still loads
    app = Pty(tmp_path, term)
    try:
        app.expect("needs 256 colours" if term == "xterm" else "Playback")
        app.send(tab("settings"))
        app.send("\x1bOB" * 2)  # past Network to Preferences
        app.expect("crowbar")
        app.quit()
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
    app.quit()
    assert app.wait_exit() == 0


def test_rebind_a_key_in_settings(app, tmp_path):
    app.expect("Playback")
    app.send(tab("settings"))
    app.expect("Force sample rate")
    show_hidden = pc.KEYS_ROW + [x for _, x in pc.BINDING_ROWS].index(pc.pk.BY_ID["devices.show_hidden"])
    app.send("\x1bOB\x1bOB\r" + "\x1bOB" * show_hidden)  # past Network to Preferences, then Devices → Show hidden in Key Bindings
    app.expect("Key Bindings")
    app.send("\r")
    app.expect("press a key for Devices · show hidden")
    app.send("q")                            # global: refused, and pwrench keeps running
    app.expect("global: quit")
    app.send("\r")
    app.expect("press a key")
    app.send("z")
    app.expect("show hidden: z")
    app.send("\x1bOA\x7f")                   # up to Mixer → Meters, Backspace unbinds
    app.expect("meters: no key")
    app.quit()
    assert app.wait_exit() == 0
    keys = json.loads((tmp_path / "pipewrench" / "ui.json").read_text())["keys"]
    assert keys == {"devices.show_hidden": "z", "mixer.meters": None}


def test_volume_at_100_asks_and_dont_ask_again_saves_the_boost(app, tmp_path):
    # Discord's stream sits at exactly 100% in the fixture.
    import pwconfig as pcfg
    import pwinventory as pi
    dump = json.loads((HERE / "fixtures" / "pwdump_patch.json").read_text())
    rows = pc.mixer_selectable(pc.mixer_view(pi.inventory(dump), pcfg.default_config(), pi.streams(dump)))
    i = next(n for n, (_, sec, r) in enumerate(rows) if r.label == "Discord" and sec == "playback")
    app.expect("Playback")
    app.send("\x1bOB" * i + "\x1bOC")     # down to Discord, then →
    app.expect("Volume past 100%")
    app.send("\x1bOD\r")                   # ← onto Don't ask again, Enter
    app.expect("boost unlocked")
    app.quit()
    assert app.wait_exit() == 0
    saved = json.loads((tmp_path / "pipewrench" / "devices.json").read_text())
    assert saved["boost"] == [rows[i][2].name]
