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
    pwctl patch [list] | connect SRC DST | disconnect SRC DST
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
import pwpatch as pp  # noqa: E402

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
            return 0, (Path(fixdir) / "pwdump_patch.json").read_text()
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
    if cmd == "patch":
        return _patch(args, runner, out)
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
       pwctl devices preview | apply [--restart] | rollback | restart"""


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
            f"{fmt:<16} {vol:<9} {row.id:>5}  {_fit(pi.connections(row), 32):<32}  {row.name}"
            + (f"  [{', '.join(tags)}]" if tags else ""))


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


RESTART_WARNING = "Apps like Discord may lose their audio connection and not reconnect."


def _write_dropin(c):
    try:
        pcfg.write_generated(pcfg.dropin_path(), pcfg.render_dropin(c))
    except pcfg.ConfigError as e:
        raise CliError(str(e), 1)


def _bus_add(runner, c, inv, name, label=None):
    """Create a bus live (no restart) and keep it in devices.json and the drop-in for every login."""
    live = next((r for r in inv.rows() if r.name == name), None)
    if live:
        raise CliError(f"{name} already exists (id {live.id}); delete it first or pick another name", 2)
    saved = next((b for b in c["buses"] if b["name"] == name), None)
    if saved:
        label = saved["label"]
    else:
        try:
            c = pcfg.add_bus(c, name, label)
        except ValueError as e:
            raise CliError(str(e), 2)
        label = label or name
        pcfg.save_config(pcfg.config_path(), c)
    _write_dropin(c)
    _run(runner, pi.bus_create_cmd(name, label))
    return f"bus {name} created; it comes back at every login"


def _bus_delete(runner, c, inv, name):
    """Destroy a virtual bus live and forget it, whether or not pwctl made it."""
    live = next((r for r in inv.rows() if r.name == name), None)
    saved = any(b["name"] == name for b in c["buses"])
    if live is None and not saved:
        raise CliError(f"no bus {name!r}", 2)
    if live is not None and live.kind != "virtual":
        raise CliError(f"{live.label} is hardware; switch its card profile instead", 2)
    if saved:
        c = pcfg.delete_bus(c, name)
        pcfg.save_config(pcfg.config_path(), c)
        _write_dropin(c)
    if live is not None:
        _run(runner, pi.destroy_cmd(live))
    return f"bus {name} removed"


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
        out.write(_bus_add(runner, c, _inventory(runner), rest[1], rest[2] if len(rest) == 3 else None) + "\n")
        return 0
    if sub == "bus" and rest[:1] == ["delete"] and len(rest) == 2:
        out.write(_bus_delete(runner, c, _inventory(runner), rest[1]) + "\n")
        return 0
    if sub == "preview" and not rest:
        pending = _generated(runner, c)
        out.write("".join(pcfg.diff_generated(p, t) for p, t, _ in pending) or "generated files are up to date\n")
        return 0
    if sub == "apply" and rest in ([], ["--restart"]):
        pending = _generated(runner, c)
        if not pending:
            out.write("generated files are up to date; nothing to do\n")
            return 0
        for p, t, _ in pending:
            out.write(pcfg.diff_generated(p, t))
            pcfg.write_generated(p, t)
            out.write(f"wrote {p}\n")
        restart, what = _restart_for([(p, r) for p, _, r in pending])
        later = (f"labels and clock changes show after {what} restarts: at next login, or now with "
                 "`pwctl devices apply --restart`\n")
        if "--restart" not in rest:
            out.write(later)
            return 0
        if ask(f"Restart {what} now? {RESTART_WARNING} [y/N] ").strip().lower() != "y":
            out.write(later)
            return 0
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
        paths = [pcfg.dropin_path(), pcfg.labels_path(_wp_version(runner))]
        try:
            for p in paths:
                pcfg.check_ours(p)
        except pcfg.ConfigError as e:
            raise CliError(str(e), 1)
        touched = [p for p in paths if p.exists() or p.with_name(p.name + ".bak").exists()]
        if not touched:
            out.write("nothing to roll back\n")
            return 0
        for p in touched:
            out.write(pcfg.rollback_generated(p) + "\n")
        out.write("takes effect at the next restart: next login, or `pwctl devices restart`\n")
        return 0
    if sub == "restart" and not rest:
        if ask(f"Restart WirePlumber now? {RESTART_WARNING} [y/N] ").strip().lower() != "y":
            out.write("nothing restarted\n")
            return 1
        _run(runner, pcfg.RESTART_WIREPLUMBER)
        return 0
    raise CliError(DEVICES_USAGE, 2)


PATCH_USAGE = "usage: pwctl patch [list] | connect SRC DST | disconnect SRC DST"


def _graph(runner):
    return pp.graph(_dump(runner))


def _endpoint(find, key, what):
    try:
        e = find(key)
    except LookupError as e:
        raise CliError(str(e), 2)
    if e is None:
        raise CliError(f"no {what} {key!r}; see `pwctl patch list`", 2)
    return e


def _patch(args, runner, out):
    sub, rest = (args[0], args[1:]) if args else ("list", [])
    if sub == "list" and not rest:
        g = _graph(runner)
        for s in g.sources:
            for d in g.targets(s):
                pairs = [f"{a.channel}→{b.channel}" for a, b, linked in pp.port_pairs(g, s, d) if linked]
                note = "" if g.state(s, d) == "full" else "  (partial)"
                out.write(f"{s.label} → {d.label}  ({', '.join(pairs)}){note}\n")
        return 0
    if sub in ("connect", "disconnect") and len(rest) == 2:
        g = _graph(runner)
        s = _endpoint(g.find_source, rest[0], "source")
        d = _endpoint(g.find_dest, rest[1], "destination")
        cmds = (pp.connect_cmds if sub == "connect" else pp.disconnect_cmds)(g, s, d)
        if not cmds:
            out.write(f"{s.label} → {d.label} is {'already connected' if sub == 'connect' else 'not connected'}\n")
            return 0
        for cmd in cmds:
            _run(runner, cmd)
        out.write(f"{sub}ed {s.label} → {d.label}\n")
        return 0
    raise CliError(PATCH_USAGE, 2)


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
        self.st = pc.UIState(theme=pc.load_ui(pc.ui_path())["theme"])
        self.current = {}
        self.presets = []
        self.block = None
        self.devices = None
        self.wp_version = None
        self.patch = None
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
            self.st.message = self.st.failed = "pw-top not found"
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
            self.st.message = self.st.failed = f"cannot read settings: {e}"
        try:
            self.presets = pc.load_presets(pc.presets_path())
        except (ValueError, OSError) as e:
            self.st.message = self.st.failed = f"presets file problem: {e}"
        if self.st.tab == "devices":
            self._refresh_devices()
        if self.st.tab == "patch":
            self._refresh_patch()

    def _refresh_patch(self):
        try:
            self.patch = _graph(self.runner)
        except CliError as e:
            self.st.message = self.st.failed = str(e)

    def _refresh_devices(self):
        """Inventory, devices.json, bus status and whether the generated files are behind."""
        try:
            c = pcfg.load_config(pcfg.config_path())
        except (ValueError, OSError) as e:
            self.st.message = self.st.failed = f"devices.json problem: {e}"
            return
        try:
            inv = _inventory(self.runner)
            if self.wp_version is None:
                self.wp_version = _wp_version(self.runner)
            dirty = bool(_generated(self.runner, c, self.wp_version))
        except CliError as e:
            self.st.message = self.st.failed = str(e)
            return
        owned, clash, pending = _bus_status(c, inv)
        self.devices = pc.devices_view(inv, c, dirty=dirty, owned=owned, clash=clash, pending=pending)

    def _main(self, scr):
        c = self.curses
        try:
            c.curs_set(0)
        except c.error:
            pass  # some terminals (vt100) can't hide the cursor
        scr.keypad(True)
        scr.timeout(250)
        c.mousemask(c.ALL_MOUSE_EVENTS)
        c.mouseinterval(150)
        self._apply_theme(scr)
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
                                                   self.devices, self.patch)
            else:
                key = self._key(ch)
                if not key:
                    continue
                self.st, actions = pc.handle_key(self.st, key, self.current, self.presets, self.devices, self.patch)
            for action in actions:
                if action == ("quit",):
                    return
                self._do(scr, action)
            if self.st.tab == "devices" and self.devices is None:
                self._refresh_devices()
            if self.st.tab == "patch" and self.patch is None:
                self._refresh_patch()

    # Without colour the roles fall back to attributes; the > and * markers carry the rest.
    _PLAIN = {"selected": "reverse bold", "anchor": "underline", "chip": "reverse", "title": "bold",
              "alert": "bold", "pending": "bold", "dim": "dim"}

    def _apply_theme(self, scr):
        """Curses attributes for each colour role of the current theme (pc.THEMES)."""
        c = self.curses
        attr = lambda names: sum(getattr(c, f"A_{n.upper()}") for n in names.split())  # noqa: E731
        if not c.has_colors():
            self.styles = {role: attr(self._PLAIN.get(role, "")) for role in pc.THEMES["dark"]}
            return
        c.use_default_colors()
        bright = c.COLORS >= 16
        theme = self.st.theme
        if c.COLORS < pc.THEME_MIN_COLOURS.get(theme, 8):
            theme = "dark"
            self.st.message = self.st.failed = f"{self.st.theme} needs {pc.THEME_MIN_COLOURS[self.st.theme]} colours; showing dark"
        exact = c.can_change_color() and c.COLORS >= 256
        slots = iter(range(min(c.COLORS, 256) - 1, 15, -1))  # palette slots for exact hex; reset when pwctl exits

        def colour(name, default):
            if name is None:
                return default
            if name.startswith("#"):
                if not exact:
                    return pc.nearest_256(name)
                n = next(slots)
                c.init_color(n, *(v * 1000 // 255 for v in pc.hex_rgb(name)))
                return n
            if name.startswith("bright"):
                return getattr(c, f"COLOR_{name[6:].upper()}") + (8 if bright else 0)
            return getattr(c, f"COLOR_{name.upper()}")

        roles = pc.THEMES[theme]
        sfg, sbg, _ = roles["screen"]
        sfg, sbg = colour(sfg, -1), colour(sbg, -1)
        self.styles = {}
        for i, (role, (fg, bg, names)) in enumerate(roles.items(), start=1):
            c.init_pair(i, colour(fg, sfg), colour(bg, sbg))
            self.styles[role] = c.color_pair(i) | attr(names)
        scr.bkgd(" ", self.styles["screen"])

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
        lines, self.hits = pc.render_screen(self.st, self.current, block, self.presets, w, h, self.devices,
                                            self.patch)
        scr.erase()
        for y, (text, style) in enumerate(lines):
            try:
                if isinstance(style, tuple):  # (base role, [(c0, c1, role), ...]) drawn in order
                    scr.addstr(y, 0, text[:w - 1] if y == h - 1 else text, self.styles[style[0]])
                    for c0, c1, role in style[1]:
                        scr.addstr(y, c0, text[c0:min(c1, w - 1 if y == h - 1 else w)], self.styles[role])
                else:
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
                self.st.failed = "" if rc == 0 else self.st.message
            elif kind == "set_theme":
                pc.save_ui(pc.ui_path(), {"theme": action[1]})
                self.st.message = f"theme: {action[1]}"
                self._apply_theme(scr)  # may replace the message: a theme the terminal can't show
            elif kind.startswith("dev_"):
                self._do_devices(scr, action)
            elif kind == "patch_cmds":
                for cmd in action[1]:
                    _run(self.runner, cmd)
                self.st.message = "unlinked" if action[1][0][1] == "-d" else "linked"
        except (CliError, ValueError, KeyError, LookupError, OSError, pcfg.ConfigError) as e:
            self.st.message = self.st.failed = str(e)
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
        elif kind == "dev_rename":
            name, old = action[1], action[2]
            label = self._prompt(scr, f"new name for {old} (empty restores the default):")
            bus = next((b for b in c["buses"] if b["name"] == name), None)
            if not label:
                # Empty restores the default: drop the custom label (a bus goes back to its own name).
                if (bus and bus["label"] == name) or (not bus and name not in c["labels"]):
                    self.st.message = f"{old} already has its default name"
                    return
                label = name if bus else None
            elif label == old:
                self.st.message = "name unchanged"
                return
            c = pcfg.set_label(c, name, label)
            pcfg.save_config(pcfg.config_path(), c)
            for path, text, _ in _generated(self.runner, c, self.wp_version):
                pcfg.write_generated(path, text)
            what = f"renamed {old} to {label}" if label else f"{old} goes back to its default name"
            if bus:
                self.st.message = f"{what}; a bus shows its new name after the next login"
            else:
                self.st.message = what
                self.st.confirm = ("restart_labels",)  # labels only show once WirePlumber reloads its rules
        elif kind == "dev_profile":
            _, card_id, index, profile = action
            _run(self.runner, ["wpctl", "set-profile", str(card_id), str(index)])
            self.st.message = f"switched to {profile}"
        elif kind == "dev_bus_create":
            self.st.message = _bus_add(self.runner, c, inv, action[1])
        elif kind == "dev_hide":
            c = pcfg.toggle_hidden(c, action[1])
            self._save_config(c, f"{'hidden' if action[1] in c['hidden'] else 'shown'}; H shows hidden rows")
        elif kind == "dev_prompt_bus":
            name = self._prompt(scr, "bus name (letters, digits, - _; empty cancels):")
            if name:
                self.st.message = _bus_add(self.runner, c, inv, name)
        elif kind == "dev_delete_bus":
            self.st.message = _bus_delete(self.runner, c, inv, action[1])
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
        """Write the generated files; never restart here. R restarts WirePlumber, after a y/n."""
        pending = _generated(self.runner, c, self.wp_version)
        if not pending:
            self.st.message = "generated files are up to date"
            return
        for p, t, _ in pending:
            pcfg.write_generated(p, t)
        _, what = _restart_for([(p, r) for p, _, r in pending])
        self.st.message = f"written; shows after {what} restarts: R now, or next login"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
