"""A PipeWire tool that hangs (pw-dump while WirePlumber restarts, Skrappjaw 2026-10-24) is a failed command,
not a crash: the runner returns exit 124 and the views show a message, then recover on the next refresh."""
import pytest

import pipewrench


def test_a_hung_command_is_a_failed_command(monkeypatch):
    monkeypatch.setattr(pipewrench, "RUN_TIMEOUT", 0.2)
    rc, out = pipewrench.default_runner(["sleep", "5"])
    assert rc == 124 and "timed out" in out and "sleep" in out


def test_a_timed_out_pw_dump_reads_as_a_cli_error():
    with pytest.raises(pipewrench.CliError, match="timed out"):
        pipewrench._dump(lambda argv: (124, "pw-dump timed out after 10 s"))


def test_normal_commands_still_run():
    assert pipewrench.default_runner(["true"]) == (0, "")


# --- the watchdog: PipeWire not answering (stage 0, 2026-10-26) ------------------------------
# The 2026-10-24 wedge: the daemon stayed up but no client got an answer, so every refresh froze the TUI
# for the whole timeout. The watchdog notices the first timeout, answers every later PipeWire call at once,
# and probes in the background until PipeWire answers again.

class Fake:
    def __init__(self):
        self.hung, self.calls = False, []

    def __call__(self, argv, timeout=None):
        self.calls.append((argv, timeout))
        if self.hung and argv[0] in pipewrench.PW_TOOLS:
            return 124, f"{argv[0]} timed out after {timeout:g} s"
        return 0, "ok"


def dog(fake):
    return pipewrench.Watchdog(fake, spawn=lambda fn: None, clock=lambda: 1000.0)


def test_pipewire_calls_get_the_short_timeout_and_others_keep_theirs():
    fake = Fake()
    w = dog(fake)
    w(["pw-dump"]), w(["busctl", "--user", "list"])
    assert fake.calls == [(["pw-dump"], pipewrench.PW_TIMEOUT), (["busctl", "--user", "list"], None)]
    assert pipewrench.PW_TIMEOUT <= 3 and not w.down


def test_a_timeout_marks_pipewire_down_and_later_calls_return_at_once():
    fake = Fake()
    w = dog(fake)
    fake.hung = True
    rc, text = w(["pw-dump"])
    assert rc == 124 and "PipeWire not answering" in text and "systemctl --user status" in text
    assert w.down
    fake.calls.clear()
    rc, text = w(["wpctl", "set-volume", "--limit", "1", "96", "5%+"])
    assert rc == 124 and "PipeWire not answering" in text and fake.calls == []  # never ran
    assert w(["busctl", "--user", "list"]) == (0, "ok")                          # not PipeWire: untouched


def test_the_probe_brings_it_back():
    fake = Fake()
    w = dog(fake)
    fake.hung = True
    w(["pw-dump"])
    assert w.probe() is False and w.down
    fake.hung = False
    assert w.probe() is True and not w.down
    assert fake.calls[-1] == (pipewrench.PROBE_CMD, pipewrench.PW_TIMEOUT)
    assert w.take_recovered() is True and w.take_recovered() is False   # "PipeWire is back" shows once
    assert w(["pw-dump"]) == (0, "ok")


def test_going_down_starts_one_probe_loop():
    started = []
    fake = Fake()
    w = pipewrench.Watchdog(fake, spawn=started.append, clock=lambda: 1000.0)
    fake.hung = True
    w(["pw-dump"]), w(["pw-metadata", "-n", "settings"])
    assert len(started) == 1


def test_the_banner_says_since_when():
    fake = Fake()
    w = dog(fake)
    assert w.banner() == ""
    fake.hung = True
    w(["pw-dump"])
    assert w.banner().startswith("PipeWire not answering since ") and "retrying every" in w.banner()


def test_the_cli_fails_fast_with_the_hint():
    fake = Fake()
    fake.hung = True
    err = __import__("io").StringIO()
    rc = pipewrench.main(["status"], runner=pipewrench.Watchdog(fake, spawn=lambda fn: None), err=err,
                         out=__import__("io").StringIO())
    assert rc != 0 and "PipeWire not answering" in err.getvalue()


def test_the_header_rule_becomes_the_banner():
    import pwcore as pc
    st = pc.UIState(tab="about", pw_down="PipeWire not answering since 20:35:10 · retrying every 5 s")
    lines, _ = pc.render_screen(st, {}, None, 100, 40)
    assert "PipeWire not answering since 20:35:10" in lines[1][0] and len(lines[1][0]) == 100
    assert lines[1][1] == "alert"
    lines, _ = pc.render_screen(pc.UIState(tab="about"), {}, None, 100, 40)
    assert lines[1][0] == "─" * 100
