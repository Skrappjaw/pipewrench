#!/usr/bin/env python3
"""pwctl: terminal PipeControl. Change PipeWire sample rate and buffer size live,
watch pw-top, and keep labelled presets.

    pwctl                      interactive UI (keyboard + mouse)
    pwctl status
    pwctl rate N | buf N | min N | max N | log N
    pwctl reset                stop forcing rate and buffer
    pwctl top                  run pw-top
    pwctl preset list | save NAME [LABEL] | apply NAME | delete NAME
    pwctl devices [list] | default | volume | mute | profile | label | hide | bus | apply …
"""
import array
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pwcore as pc  # noqa: E402
import pwconfig as pcfg  # noqa: E402
import pwinventory as pi  # noqa: E402

SETTERS = {"rate": "clock.force-rate", "buf": "clock.force-quantum", "min": "clock.min-quantum",
           "max": "clock.max-quantum", "log": "log.level"}
USAGE = __doc__.split("\n\n", 1)[1]
SETTLE_TRIES = 5


def SETTLE():
    """Wait for PipeWire to come back after a restart; tests replace this."""
    time.sleep(1)


def default_runner(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    return p.returncode, p.stdout + p.stderr


def fixture_runner(fixdir):
    """PWCTL_FIXTURES mode: reads come from recorded output, writes are accepted and dropped."""
    def run(argv):
        if argv == pc.READ_CMD:
            return 0, (Path(fixdir) / "settings.txt").read_text()
        if argv == pcfg.VERSION_CMD:
            return 0, "wireplumber\nCompiled with libwireplumber 0.4.17\n"
        if argv == pi.DUMP_CMD:
            return 0, (Path(fixdir) / "pwdump_inventory.json").read_text()
        return 0, ""
    return run


def pwtop_proc(iterations=None):
    fixdir = os.environ.get("PWCTL_FIXTURES")
    if fixdir:
        argv = ["cat", str(Path(fixdir) / "pwtop_batch.txt")]
    else:
        argv = ["pw-top", "-b"] + (["-n", str(iterations)] if iterations else [])
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)


def pwtop_lines(iterations=None):
    proc = pwtop_proc(iterations)
    try:
        for line in proc.stdout:
            yield line.rstrip("\n")
    finally:
        proc.terminate()
        proc.wait()


# --- CLI ---------------------------------------------------------------------

class CliError(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def _read(runner):
    try:
        return pc.read_settings(runner)
    except FileNotFoundError:
        raise CliError("pw-metadata not found; is PipeWire installed?", 1)
    except pc.MetadataError as e:
        raise CliError(f"could not read PipeWire settings: {e}", 1)


def _write(runner, cmds):
    for cmd in cmds:
        try:
            rc, out = runner(cmd)
        except FileNotFoundError:
            raise CliError("pw-metadata not found; is PipeWire installed?", 1)
        if rc != 0:
            raise CliError(f"{' '.join(cmd[4:])}: {out.strip() or f'exit {rc}'}", 1)


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        raise CliError(f"not a whole number: {text!r}", 2)


def _cli(argv, runner, out, err, execvp, ask):
    cmd, args = argv[0], argv[1:]
    if cmd in ("-h", "--help", "help"):
        out.write(USAGE)
        return 0
    if cmd == "status":
        cur = _read(runner)
        for s in pc.SETTINGS:
            out.write(f"{s.short:<14} {pc.display_value(s, cur.get(s.key))}\n")
        allowed = cur.get("clock.allowed-rates")
        out.write(f"{'allowed rates':<14} {', '.join(map(str, allowed)) if isinstance(allowed, list) else '?'}\n")
        out.write(f"{'defaults':<14} rate {cur.get('clock.rate', '?')} · quantum {cur.get('clock.quantum', '?')}\n")
        return 0
    if cmd in SETTERS:
        if len(args) != 1:
            raise CliError(f"usage: pwctl {cmd} N", 2)
        key, value = SETTERS[cmd], _int(args[0])
        try:
            cmds = [pc.set_cmd(key, value)]
            cur = _read(runner)
            warnings = pc.validate(cur, {key: value})
        except ValueError as e:
            raise CliError(str(e), 2)
        for w in warnings:
            err.write(f"warning: {w}\n")
        _write(runner, cmds)
        return 0
    if cmd == "reset":
        _write(runner, [pc.set_cmd("clock.force-rate", 0), pc.set_cmd("clock.force-quantum", 0)])
        return 0
    if cmd == "top":
        (execvp or os.execvp)("pw-top", ["pw-top"])
        return 0
    if cmd == "preset":
        return _preset(args, runner, out)
    if cmd == "devices":
        return _devices(args, runner, out, err, ask)
    raise CliError(f"unknown command {cmd!r}\n\nusage:\n{USAGE}", 2)


def _preset(args, runner, out):
    path = pc.presets_path()
    presets = pc.load_presets(path)
    sub, rest = (args[0], args[1:]) if args else ("list", [])
    if sub == "list":
        if not presets:
            out.write("no presets saved\n")
        for p in presets:
            out.write(f"{p['name']:<16} {p['label']:<28} {pc.summarize_preset(p)}\n")
        return 0
    if sub == "save" and 1 <= len(rest) <= 2:
        try:
            p = pc.capture_preset(_read(runner), rest[0], rest[1] if len(rest) > 1 else rest[0])
            presets = pc.add_preset(presets, p)
        except ValueError as e:
            raise CliError(str(e), 2)
        pc.save_presets(path, presets)
        out.write(f"saved {p['name']}: {pc.summarize_preset(p)}\n")
        return 0
    if sub in ("apply", "delete") and len(rest) == 1:
        p = pc.find_preset(presets, rest[0])
        if not p:
            raise CliError(f"no preset named {rest[0]!r}", 2)
        if sub == "apply":
            _write(runner, pc.preset_cmds(p, _read(runner)))
            out.write(f"applied {p['name']}: {pc.summarize_preset(p)}\n")
        else:
            pc.save_presets(path, pc.delete_preset(presets, p["name"]))
            out.write(f"deleted {p['name']}\n")
        return 0
    raise CliError("usage: pwctl preset list | save NAME [LABEL] | apply NAME | delete NAME", 2)




def _dump(runner):
    try:
        return pi.read_dump(runner)
    except FileNotFoundError:
        raise CliError("pw-dump not found; is PipeWire installed?", 1)
    except (pi.InventoryError, ValueError) as e:
        raise CliError(f"could not read pw-dump: {e}", 1)


def write_noise(path, seconds=1.0, rate=48000, peak=4000):
    """A short, quiet stereo noise burst for telling pairs apart by ear."""
    rnd = random.Random(0)
    samples = array.array("h", (rnd.randint(-peak, peak) for _ in range(int(seconds * rate) * 2)))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


DEVICES_USAGE = """usage: pwctl devices [list] [--hidden] [--monitors]
       pwctl devices default DEV | volume DEV STEP | mute DEV | test DEV
       pwctl devices profile CARD PROFILE
       pwctl devices label DEV TEXT | label DEV --clear | hide NAME | unhide NAME
       pwctl devices bus add NAME [LABEL] | bus delete NAME
       pwctl devices preview | apply | rollback"""


def _play_noise(runner, row):
    if row.kind == "monitor" or row.direction not in ("out", "duplex"):
        raise CliError(f"{row.label} is not an output", 2)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "pwctl-test.wav"
        write_noise(wav)
        _run(runner, ["pw-cat", "--playback", "--target", row.name, str(wav)])
    return f"played noise on {row.label}"


def _inventory(runner):
    return pi.inventory(_dump(runner))


def _config():
    try:
        return pcfg.load_config(pcfg.config_path())
    except ValueError as e:
        raise CliError(f"{pcfg.config_path()}: {e}", 2)


def _save(c, out, message):
    pcfg.save_config(pcfg.config_path(), c)
    out.write(f"{message}; `pwctl devices apply` to use it\n")


def _row(inv, key):
    try:
        row = inv.find(key)
    except LookupError as e:
        raise CliError(str(e), 2)
    if row is None:
        raise CliError(f"no device {key!r}; see `pwctl devices list`", 2)
    return row


def _run(runner, cmd):
    try:
        rc, text = runner(cmd)
    except FileNotFoundError:
        raise CliError(f"{cmd[0]} not found", 1)
    if rc != 0:
        raise CliError(f"{' '.join(cmd)}: {text.strip() or f'exit {rc}'}", 1)


def _wp_version(runner):
    try:
        return pcfg.wireplumber_version(runner)
    except FileNotFoundError:
        raise CliError("wireplumber not found", 1)
    except pcfg.ConfigError as e:
        raise CliError(str(e), 1)


def _fit(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def device_line(row, c, hidden=False, owned=(), clash=()):
    fmt = " ".join(x for x in (row.format, f"{row.channels}ch" if row.channels else None,
                               str(row.rate) if row.rate else None) if x)
    vol = "muted" if row.mute else "" if row.volume is None else f"vol {round(row.volume * 100)}%"
    pending = c["labels"].get(row.name)
    tags = [t for t, on in (("pwctl", row.name in owned), ("made outside pwctl; in devices.json too", row.name in clash),
                            ("hidden", hidden), (f"label → {pending}", bool(pending) and pending != row.label)) if on]
    return (f"  {'★' if row.default else ' '} {row.direction:<6} {_fit(row.label, 30):<30} {row.state or '?':<9} "
            f"{fmt:<16} {vol:<9} {row.id:>5}  {row.name}" + (f"  [{', '.join(tags)}]" if tags else ""))


def _bus_status(c, inv):
    """(owned, clash, pending): buses pwctl's drop-in defines, live buses with a devices.json name that
    pwctl didn't make, and devices.json buses not running yet."""
    dropin = pcfg.dropin_path()
    applied = dropin.read_text() if dropin.exists() else ""
    live = {r.name for r in inv.rows()}
    owned = {b["name"] for b in c["buses"] if f'node.name = "{b["name"]}"' in applied}
    clash = set(pcfg.bus_conflicts(c, live, applied))
    pending = [b for b in c["buses"] if b["name"] not in live]
    return owned, clash, pending


def _generated(runner, c, version=None):
    """(path, text, restart) for the drop-in and the label rules; only files with something to say
    are created, but an existing file is always kept in step."""
    version = version or _wp_version(runner)
    files = [(pcfg.dropin_path(), pcfg.render_dropin(c), pcfg.RESTART_PIPEWIRE,
              bool(c["buses"] or c["clock_rate"])),
             (pcfg.labels_path(version), pcfg.render_labels(c, version), pcfg.RESTART_WIREPLUMBER,
              bool(c["labels"]))]
    pending = []
    for path, text, restart, has_content in files:
        try:
            pcfg.check_ours(path)
        except pcfg.ConfigError as e:
            raise CliError(str(e), 1)
        if (path.exists() or has_content) and pcfg.diff_generated(path, text):
            pending.append((path, text, restart))
    return pending


def _restart_for(paths_and_restarts):
    restarts = [r for _, r in paths_and_restarts]
    if pcfg.RESTART_PIPEWIRE in restarts:
        return pcfg.RESTART_PIPEWIRE, "PipeWire"
    return pcfg.RESTART_WIREPLUMBER, "WirePlumber"


def _settled_problems(c, runner):
    for _ in range(SETTLE_TRIES):
        SETTLE()
        problems = pcfg.check_applied(c, {r.name: r.label for r in _inventory(runner).rows()})
        if not problems:
            break
    return problems


def _devices(args, runner, out, err, ask):
    sub, rest = (args[0], args[1:]) if args else ("list", [])
    c = _config()
    if sub == "list" and set(rest) <= {"--hidden", "--monitors"}:
        inv = _inventory(runner)
        hidden = set(c["hidden"])
        owned, clash, pending = _bus_status(c, inv)
        for heading, row in pi.view(inv, hidden=hidden, show_hidden="--hidden" in rest, monitors="--monitors" in rest):
            out.write(f"{heading}\n" if row is None else device_line(row, c, row.name in hidden, owned, clash) + "\n")
        for b in pending:
            out.write(f"  + {'duplex':<6} {_fit(b['label'], 30):<30} pending: in devices.json, "
                      f"`pwctl devices apply` creates it  {b['name']}\n")
        return 0
    if sub in ("default", "mute", "test") and len(rest) == 1 or sub == "volume" and len(rest) == 2:
        row = _row(_inventory(runner), rest[0])
        try:
            cmd = {"default": pi.default_cmd, "mute": pi.mute_cmd}.get(sub, lambda r: None)(row) \
                if sub != "volume" else pi.volume_cmd(row, rest[1])
        except ValueError as e:
            raise CliError(str(e), 2)
        if sub == "test":
            out.write(_play_noise(runner, row) + "\n")
        else:
            _run(runner, cmd)
        return 0
    if sub == "profile" and len(rest) == 2:
        card = _inventory(runner).find_card(rest[0])
        if card is None:
            raise CliError(f"no card {rest[0]!r}; see `pwctl devices list`", 2)
        try:
            cmd = pi.profile_cmd(card, rest[1])
        except ValueError as e:
            raise CliError(str(e), 2)
        if ask(f"Switch {card.label} to {rest[1]}? Audio on that card drops for a moment. [y/N] ").strip().lower() != "y":
            out.write("nothing changed\n")
            return 1
        _run(runner, cmd)
        return 0
    if sub == "label" and len(rest) == 2:
        inv = _inventory(runner)
        name = rest[0] if any(b["name"] == rest[0] for b in c["buses"]) else _row(inv, rest[0]).name
        try:
            c = pcfg.set_label(c, name, None if rest[1] == "--clear" else rest[1])
        except ValueError as e:
            raise CliError(str(e), 2)
        _save(c, out, f"label for {name} saved")
        return 0
    if sub in ("hide", "unhide") and len(rest) == 1:
        inv = _inventory(runner)
        card = inv.find_card(rest[0])
        name = card.name if card else rest[0] if rest[0] in c["hidden"] else _row(inv, rest[0]).name
        if (name in c["hidden"]) != (sub == "unhide"):
            out.write(f"{name} is already {'hidden' if sub == 'hide' else 'shown'}\n")
            return 0
        pcfg.save_config(pcfg.config_path(), pcfg.toggle_hidden(c, name))
        out.write(f"{sub} {name}\n")
        return 0
    if sub == "bus" and rest[:1] == ["add"] and len(rest) in (2, 3):
        try:
            c = pcfg.add_bus(c, rest[1], rest[2] if len(rest) == 3 else None)
        except ValueError as e:
            raise CliError(str(e), 2)
        _save(c, out, f"bus {rest[1]} saved")
        return 0
    if sub == "bus" and rest[:1] == ["delete"] and len(rest) == 2:
        try:
            c = pcfg.delete_bus(c, rest[1])
        except KeyError:
            raise CliError(f"no bus {rest[1]!r}", 2)
        _save(c, out, f"bus {rest[1]} deleted")
        return 0
    if sub == "preview" and not rest:
        pending = _generated(runner, c)
        out.write("".join(pcfg.diff_generated(p, t) for p, t, _ in pending) or "generated files are up to date\n")
        return 0
    if sub == "apply" and not rest:
        pending = _generated(runner, c)
        if not pending:
            out.write("generated files are up to date; nothing to do\n")
            return 0
        for p, t, _ in pending:
            out.write(pcfg.diff_generated(p, t))
        dropin = pcfg.dropin_path()
        clash = pcfg.bus_conflicts(c, {r.name for r in _inventory(runner).rows()},
                                   dropin.read_text() if dropin.exists() else "")
        if clash:
            err.write(f"warning: bus {', '.join(clash)} already exists outside pwctl (e.g. made by a script); "
                      "stop that first, or there will be two\n")
        restart, what = _restart_for([(p, r) for p, _, r in pending])
        if ask(f"Write {len(pending)} file(s) and restart {what}? Audio drops for a moment. [y/N] ").strip().lower() != "y":
            out.write("nothing written\n")
            return 1
        for p, t, _ in pending:
            pcfg.write_generated(p, t)
            out.write(f"wrote {p}\n")
        _run(runner, restart)
        problems = _settled_problems(c, runner)
        if problems:
            for p in problems:
                err.write(f"  {p}\n")
            err.write("not everything showed up; `pwctl devices list` again, or `pwctl devices rollback` to undo\n")
            return 1
        out.write("applied\n")
        return 0
    if sub == "rollback" and not rest:
        paths = [(pcfg.dropin_path(), pcfg.RESTART_PIPEWIRE), (pcfg.labels_path(_wp_version(runner)), pcfg.RESTART_WIREPLUMBER)]
        try:
            for p, _ in paths:
                pcfg.check_ours(p)
        except pcfg.ConfigError as e:
            raise CliError(str(e), 1)
        touched = [(p, r) for p, r in paths if p.exists() or p.with_name(p.name + ".bak").exists()]
        if not touched:
            out.write("nothing to roll back\n")
            return 0
        restart, what = _restart_for(touched)
        if ask(f"Roll back {len(touched)} file(s) and restart {what}? [y/N] ").strip().lower() != "y":
            out.write("nothing changed\n")
            return 1
        for p, _ in touched:
            out.write(pcfg.rollback_generated(p) + "\n")
        _run(runner, restart)
        return 0
    raise CliError(DEVICES_USAGE, 2)


def main(argv, runner=None, out=None, err=None, execvp=None, ask=None):
    out, err = out or sys.stdout, err or sys.stderr
    ask = ask or input
    if runner is None:
        fixdir = os.environ.get("PWCTL_FIXTURES")
        runner = fixture_runner(fixdir) if fixdir else default_runner
    try:
        if not argv:
            return Tui(runner).run()
        return _cli(argv, runner, out, err, execvp, ask)
    except CliError as e:
        err.write(f"pwctl: {e}\n")
        return e.code


# --- curses UI ---------------------------------------------------------------

class Tui:
    def __init__(self, runner):
        self.runner = runner
        self.st = pc.UIState()
        self.current = {}
        self.presets = []
        self.block = None
        self.devices = None
        self.wp_version = None
        self.lock = threading.Lock()
        self.proc = None
        self.hits = []

    def run(self):
        import curses
        os.environ.setdefault("ESCDELAY", "25")
        self.curses = curses
        try:
            curses.wrapper(self._main)
        finally:
            if self.proc:
                self.proc.terminate()
        return 0

    # background pw-top reader
    def _pwtop(self):
        parser = pc.TopParser()
        try:
            self.proc = pwtop_proc()
        except FileNotFoundError:
            self.st.message = "pw-top not found"
            return
        for line in self.proc.stdout:
            done = parser.feed(line.rstrip("\n"))
            if done is not None:
                with self.lock:
                    self.block = done
        last = parser.flush()
        if last:
            with self.lock:
                self.block = last

    def _refresh(self):
        try:
            self.current = pc.read_settings(self.runner)
        except (pc.MetadataError, FileNotFoundError) as e:
            self.st.message = f"cannot read settings: {e}"
        try:
            self.presets = pc.load_presets(pc.presets_path())
        except (ValueError, OSError) as e:
            self.st.message = f"presets file problem: {e}"
        if self.st.tab == "devices":
            self._refresh_devices()

    def _refresh_devices(self):
        """Inventory, devices.json, bus status and whether the generated files are behind."""
        try:
            c = pcfg.load_config(pcfg.config_path())
        except (ValueError, OSError) as e:
            self.st.message = f"devices.json problem: {e}"
            return
        try:
            inv = _inventory(self.runner)
            if self.wp_version is None:
                self.wp_version = _wp_version(self.runner)
            dirty = bool(_generated(self.runner, c, self.wp_version))
        except CliError as e:
            self.st.message = str(e)
            return
        owned, clash, pending = _bus_status(c, inv)
        self.devices = pc.devices_view(inv, c, dirty=dirty, owned=owned, clash=clash, pending=pending)

    def _main(self, scr):
        c = self.curses
        c.curs_set(0)
        scr.keypad(True)
        scr.timeout(250)
        c.mousemask(c.ALL_MOUSE_EVENTS)
        c.mouseinterval(150)
        self.styles = {"normal": c.A_NORMAL, "dim": c.A_DIM, "selected": c.A_REVERSE,
                       "title": c.A_BOLD, "button": c.A_BOLD, "alert": c.A_BOLD}
        if c.has_colors():
            c.use_default_colors()
            for i, (name, fg) in enumerate((("title", c.COLOR_CYAN), ("button", c.COLOR_GREEN),
                                            ("alert", c.COLOR_RED)), start=1):
                c.init_pair(i, fg, -1)
                self.styles[name] = c.color_pair(i) | c.A_BOLD
        threading.Thread(target=self._pwtop, daemon=True).start()
        self._refresh()
        last_refresh = time.monotonic()
        while True:
            self._draw(scr)
            ch = scr.getch()
            if time.monotonic() - last_refresh > 1.0:
                self._refresh()
                last_refresh = time.monotonic()
            if ch == -1:
                continue
            if ch == c.KEY_MOUSE:
                try:
                    _, x, y, _, b = c.getmouse()
                except c.error:
                    continue
                event = self._mouse_event(b)
                if not event:
                    continue
                self.st, actions = pc.handle_mouse(self.st, self.hits, y, x, event, self.current, self.presets,
                                                   self.devices)
            else:
                key = self._key(ch)
                if not key:
                    continue
                self.st, actions = pc.handle_key(self.st, key, self.current, self.presets, self.devices)
            for action in actions:
                if action == ("quit",):
                    return
                self._do(scr, action)
            if self.st.tab == "devices" and self.devices is None:
                self._refresh_devices()

    def _mouse_event(self, b):
        c = self.curses
        if b & c.BUTTON1_DOUBLE_CLICKED:
            return "double"
        if b & (c.BUTTON1_CLICKED | c.BUTTON1_PRESSED):
            return "click"
        if b & c.BUTTON4_PRESSED:
            return "wheel_up"
        if b & getattr(c, "BUTTON5_PRESSED", 0x200000):
            return "wheel_down"
        return None

    def _key(self, ch):
        c = self.curses
        named = {c.KEY_UP: "up", c.KEY_DOWN: "down", c.KEY_LEFT: "left", c.KEY_RIGHT: "right",
                 c.KEY_PPAGE: "pgup", c.KEY_NPAGE: "pgdn", c.KEY_ENTER: "enter", 10: "enter", 13: "enter",
                 27: "esc", 9: "tab"}
        if ch in named:
            return named[ch]
        if 32 <= ch < 127:
            return chr(ch)
        return None

    def _draw(self, scr):
        h, w = scr.getmaxyx()
        with self.lock:
            block = self.block
        lines, self.hits = pc.render_screen(self.st, self.current, block, self.presets, w, h, self.devices)
        scr.erase()
        for y, (text, style) in enumerate(lines):
            try:
                scr.addstr(y, 0, text[:w - 1] if y == h - 1 else text, self.styles.get(style, 0))
            except self.curses.error:
                pass
        scr.refresh()

    def _prompt(self, scr, question, default=""):
        c = self.curses
        h, w = scr.getmaxyx()
        scr.move(h - 1, 0)
        scr.clrtoeol()
        scr.addstr(h - 1, 0, f" {question}"[:w - 1], self.styles["alert"])
        c.echo()
        c.curs_set(1)
        scr.timeout(-1)
        try:
            raw = scr.getstr(h - 1, min(len(question) + 2, w - 2), 60)
            answer = raw.decode(errors="replace").strip()
        finally:
            c.noecho()
            c.curs_set(0)
            scr.timeout(250)
        return answer or default

    def _do(self, scr, action):
        kind = action[0]
        try:
            if kind == "set_many":
                warn = self.st.message
                _write(self.runner, [pc.set_cmd(k, v) for k, v in action[1]])
                self.st.message = warn or "applied: " + ", ".join(f"{k.split('.')[-1]}={v}" for k, v in action[1])
            elif kind == "apply_preset":
                p = pc.find_preset(self.presets, action[1])
                _write(self.runner, pc.preset_cmds(p, self.current))
                self.st.message = f"applied preset {p['name']}: {pc.summarize_preset(p)}"
            elif kind == "prompt_save":
                name = self._prompt(scr, "preset name (letters, digits, - _; empty cancels):")
                if not name:
                    self.st.message = "cancelled"
                    return
                label = self._prompt(scr, f"label for {name}:", default=name)
                self.presets = pc.add_preset(self.presets, pc.capture_preset(self.current, name, label))
                pc.save_presets(pc.presets_path(), self.presets)
                self.st.message = f"saved preset {name}"
            elif kind == "prompt_label":
                label = self._prompt(scr, f"new label for {action[1]}:")
                if label:
                    self.presets = pc.relabel_preset(self.presets, action[1], label)
                    pc.save_presets(pc.presets_path(), self.presets)
                    self.st.message = f"relabelled {action[1]}"
            elif kind == "delete_preset":
                self.presets = pc.delete_preset(self.presets, action[1])
                pc.save_presets(pc.presets_path(), self.presets)
                self.st.message = f"deleted preset {action[1]}"
            elif kind == "restart_wireplumber":
                rc, out = self.runner(pc.RESTART_CMD)
                self.st.message = "WirePlumber restarted" if rc == 0 else f"restart failed: {out.strip()}"
            elif kind.startswith("dev_"):
                self._do_devices(scr, action)
        except (CliError, ValueError, KeyError, LookupError, OSError, pcfg.ConfigError) as e:
            self.st.message = str(e)
        self._refresh()

    def _save_config(self, c, message):
        pcfg.save_config(pcfg.config_path(), c)
        self.st.message = message

    def _do_devices(self, scr, action):
        kind = action[0]
        c, inv = self.devices["config"], self.devices["inv"]
        if kind == "dev_cmd":
            _run(self.runner, action[1])
            verb = {"set-default": "default set", "set-volume": "volume", "set-mute": "mute toggled"}
            self.st.message = verb.get(action[1][1], " ".join(action[1]))
        elif kind == "dev_prompt_label":
            name, old = action[1], action[2]
            label = self._prompt(scr, f"label for {old} (empty clears a label, Esc-Enter keeps it):")
            self._save_config(pcfg.set_label(c, name, label or None),
                              f"label saved for {old}; A applies it" if label else f"label cleared for {old}; A applies it")
        elif kind == "dev_prompt_profile":
            card = inv.find_card(action[1])
            choices = ", ".join(f"{p.index} {p.name}" for p in card.profiles)
            answer = self._prompt(scr, f"profile for {card.label} ({choices}; empty cancels):")
            if not answer:
                self.st.message = "cancelled"
                return
            _run(self.runner, pi.profile_cmd(card, answer))
            self.st.message = f"{card.label} switched to {answer}"
        elif kind == "dev_hide":
            c = pcfg.toggle_hidden(c, action[1])
            self._save_config(c, f"{'hidden' if action[1] in c['hidden'] else 'shown'}; H shows hidden rows")
        elif kind == "dev_prompt_bus":
            name = self._prompt(scr, "bus name (letters, digits, - _; empty cancels):")
            if name:
                self._save_config(pcfg.add_bus(c, name), f"bus {name} saved; A creates it")
        elif kind == "dev_delete_bus":
            self._save_config(pcfg.delete_bus(c, action[1]), f"bus {action[1]} deleted; A applies it")
        elif kind == "dev_preview":
            diff = "".join(pcfg.diff_generated(p, t) for p, t, _ in _generated(self.runner, c, self.wp_version))
            if not diff:
                self.st.message = "generated files are up to date"
                return
            self.curses.endwin()
            try:
                subprocess.run([os.environ.get("PAGER", "less")], input=diff, text=True)
            except FileNotFoundError:
                self.st.message = "no pager; run `pwctl devices preview`"
            scr.refresh()
        elif kind == "dev_test":
            self.st.message = _play_noise(self.runner, inv.find(action[1]))
        elif kind == "dev_apply":
            self._apply_devices(scr, c)

    def _apply_devices(self, scr, c):
        pending = _generated(self.runner, c, self.wp_version)
        if not pending:
            self.st.message = "generated files are up to date"
            return
        restart, what = _restart_for([(p, r) for p, _, r in pending])
        for p, t, _ in pending:
            pcfg.write_generated(p, t)
        self.st.message = f"restarting {what}…"
        self._draw(scr)
        _run(self.runner, restart)
        problems = _settled_problems(c, self.runner)
        if restart == pcfg.RESTART_PIPEWIRE:
            threading.Thread(target=self._pwtop, daemon=True).start()  # the old pw-top died with PipeWire
        self.st.message = (f"{len(problems)} problem(s): {problems[0]}; see `pwctl devices list`" if problems
                           else f"applied; {what} restarted")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
