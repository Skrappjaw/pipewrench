#!/usr/bin/env python3
"""pwctl: terminal PipeControl. Change PipeWire sample rate and buffer size live,
watch pw-top, and keep labelled presets.

    pwctl                      interactive UI (keyboard + mouse)
    pwctl status
    pwctl rate N | buf N | min N | max N | log N
    pwctl reset                stop forcing rate and buffer
    pwctl top                  run pw-top
    pwctl preset list | save NAME [LABEL] | apply NAME | delete NAME
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pwcore as pc  # noqa: E402

SETTERS = {"rate": "clock.force-rate", "buf": "clock.force-quantum", "min": "clock.min-quantum",
           "max": "clock.max-quantum", "log": "log.level"}
USAGE = __doc__.split("\n\n", 1)[1]


def default_runner(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    return p.returncode, p.stdout + p.stderr


def fixture_runner(fixdir):
    """PWCTL_FIXTURES mode: reads come from recorded output, writes are accepted and dropped."""
    def run(argv):
        if argv == pc.READ_CMD:
            return 0, (Path(fixdir) / "settings.txt").read_text()
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


def _cli(argv, runner, out, err, execvp):
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


def main(argv, runner=None, out=None, err=None, execvp=None):
    out, err = out or sys.stdout, err or sys.stderr
    if runner is None:
        fixdir = os.environ.get("PWCTL_FIXTURES")
        runner = fixture_runner(fixdir) if fixdir else default_runner
    try:
        if not argv:
            return Tui(runner).run()
        return _cli(argv, runner, out, err, execvp)
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
                self.st, actions = pc.handle_mouse(self.st, self.hits, y, x, event, self.current, self.presets)
            else:
                key = self._key(ch)
                if not key:
                    continue
                self.st, actions = pc.handle_key(self.st, key, self.current, self.presets)
            for action in actions:
                if action == ("quit",):
                    return
                self._do(scr, action)

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
        lines, self.hits = pc.render_screen(self.st, self.current, block, self.presets, w, h)
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
        except (CliError, ValueError, KeyError, OSError) as e:
            self.st.message = str(e)
        self._refresh()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
