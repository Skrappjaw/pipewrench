"""Realtime status for the Settings tab and `pwrench realtime`: is PipeWire's audio thread realtime, and
if not, the command that fixes it. Reads a /proc tree and limits files passed in, so tests use tmp dirs."""
import io
import re

import pytest

import pwcore as pc
import pipewrench
import pwrealtime as prt

LIMITS = "# comment\n@pipewire   - rtprio  95\n@pipewire   - nice    -19\n@pipewire   - memlock 4194304\n"


def stat(tid, comm, policy, prio, ppid=1):
    # fields: pid (comm) state ppid ... rt_priority is field 40, policy 41
    rest = ["S", str(ppid)] + ["0"] * 35 + [str(prio), str(policy)] + ["0"] * 11
    return f"{tid} ({comm}) " + " ".join(rest) + "\n"


def make_proc(tmp_path, rt=False, limit=0, procs=(("pipewire", 100), ("pipewire-pulse", 200))):
    proc = tmp_path / "proc"
    for comm, pid in procs:
        d = proc / str(pid)
        (d / "task").mkdir(parents=True)
        (d / "comm").write_text(comm + "\n")
        (d / "stat").write_text(stat(pid, comm, 0, 0, ppid=42))
        (d / "limits").write_text(f"Limit  Soft Limit  Hard Limit  Units\nMax realtime priority     {limit}                    {limit}\n")
        for tid, tcomm in ((pid, comm), (pid + 1, "module-rt"), (pid + 2, "pw-data-loop")):
            t = d / "task" / str(tid)
            t.mkdir()
            t.joinpath("comm").write_text(tcomm + "\n")
            loop = tcomm == "pw-data-loop" and rt
            t.joinpath("stat").write_text(stat(tid, tcomm, 1 if loop else 0, 88 if loop else 0))
    other = proc / "999"
    other.mkdir(parents=True)
    (other / "comm").write_text("bash\n")
    return proc


def make_limits(tmp_path, text=LIMITS, name="25-pw-rlimits.conf"):
    d = tmp_path / "limits.d"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text)
    (d / "audio.conf.disabled").write_text("@audio - rtprio 95\n")
    return [d / "../limits.conf", d]


def run_ok(n=0):
    def run(argv):
        assert argv[0] == "journalctl"
        return 0, "x: Realtime error: Could not get pidns\n" * n + "other line\n"
    return run


def check(tmp_path, rt=False, limit=0, member=True, refusals=0, **kw):
    return prt.check(proc=make_proc(tmp_path, rt=rt, limit=limit, **kw), limits=make_limits(tmp_path),
                     user="alex", members=lambda g: ["alex"] if member and g == "pipewire" else [],
                     run=run_ok(refusals))


def test_limits_groups_reads_conf_files_only(tmp_path):
    assert prt.limits_groups(make_limits(tmp_path)) == {"pipewire": 95}


def test_limits_groups_skips_comments_and_zero(tmp_path):
    paths = make_limits(tmp_path, "#@pipewire - rtprio 95\n@audio - rtprio 0\nalex - nice -5\n")
    assert prt.limits_groups(paths) == {}


def test_threads_read_from_proc(tmp_path):
    procs = prt.pipewire_procs(make_proc(tmp_path, rt=True, limit=95))
    assert [(p.comm, p.limit, p.ppid) for p in procs] == [("pipewire", 95, 42), ("pipewire-pulse", 95, 42)]
    assert all(t.realtime and t.prio == 88 for p in procs for t in p.loops)


def test_all_good(tmp_path):
    s = check(tmp_path, rt=True, limit=95)
    assert s.ok
    assert prt.fix(s) == ["PipeWire audio runs realtime"]


def test_member_but_limit_zero_says_reboot(tmp_path):
    s = check(tmp_path, limit=0)
    assert not s.ok
    lines = prt.fix(s)
    assert "reboot" in lines[0]
    assert any("prlimit --pid 42 --rtprio=95:95" in x for x in lines)


def test_not_a_member_says_usermod(tmp_path):
    s = check(tmp_path, member=False)
    assert prt.fix(s)[0] == "sudo usermod -aG pipewire alex, then reboot"


def test_limit_ok_but_threads_not_realtime_says_restart(tmp_path):
    s = check(tmp_path, rt=False, limit=95)
    assert any("systemctl --user restart pipewire pipewire-pulse wireplumber" in x for x in prt.fix(s))


def test_no_group_granted(tmp_path):
    s = prt.check(proc=make_proc(tmp_path), limits=make_limits(tmp_path, "# nothing\n"), user="alex",
                  members=lambda g: [], run=run_ok())
    assert "no file in /etc/security/limits.d" in prt.fix(s)[0]


def test_pipewire_not_running(tmp_path):
    s = check(tmp_path, procs=())
    assert not s.ok
    assert any("isn't running" in text for _, _, text in prt.rows(s))


def test_rows_marks(tmp_path):
    rows = prt.rows(check(tmp_path, limit=0, refusals=30))
    assert [label for label, _, _ in rows] == ["pipewire group", "realtime limit", "audio threads", "portal refusals"]
    marks = {label: mark for label, mark, _ in rows}
    assert marks == {"pipewire group": "●", "realtime limit": "◐", "audio threads": "◐", "portal refusals": ""}
    text = {label: t for label, _, t in rows}
    assert text["audio threads"] == "2 of 2 not realtime"
    assert text["portal refusals"].startswith("30 in the last 10 min")


def test_refusals_unknown_when_journal_fails(tmp_path):
    def run(argv):
        raise OSError("no journalctl")
    s = prt.check(proc=make_proc(tmp_path), limits=make_limits(tmp_path), user="alex",
                  members=lambda g: ["alex"], run=run)
    assert s.refusals is None


# --- Settings tab ---------------------------------------------------------------

def settings(width=100, height=50, realtime=None):
    return pc.render_screen(pc.UIState(tab="settings"), {}, [], [], width, height, realtime=realtime)[0]


def test_section_between_clock_and_display(tmp_path):
    lines = [t.strip() for t, _ in settings(realtime=check(tmp_path))]
    assert lines.index("Clock") < lines.index("Realtime") < lines.index("Display")


def test_section_shows_rows_and_fix(tmp_path):
    body = "\n".join(t for t, _ in settings(realtime=check(tmp_path, limit=0)))
    assert "realtime limit" in body and "◐" in body
    assert "fix: reboot" in body


def test_section_checking_when_none():
    assert any("checking" in t for t, _ in settings(realtime=None))


@pytest.mark.parametrize("width", [40, 80, 120])
def test_section_fits_width(tmp_path, width):
    assert all(len(t) <= width for t, _ in settings(width=width, realtime=check(tmp_path)))


def test_cursor_rows_unchanged():
    assert pc.THEME_ROW == len(pc.SETTINGS)


# --- CLI ------------------------------------------------------------------------

def test_cli_realtime(tmp_path, monkeypatch):
    status = check(tmp_path, limit=0)
    monkeypatch.setattr(prt, "check", lambda run: status)
    out = io.StringIO()
    assert pipewrench.main(["realtime"], runner=run_ok(), out=out) == 1
    assert "realtime limit" in out.getvalue()
    assert re.search(r"^fix: +reboot", out.getvalue(), re.M)


def test_cli_realtime_ok(tmp_path, monkeypatch):
    status = check(tmp_path, rt=True, limit=95)
    monkeypatch.setattr(prt, "check", lambda run: status)
    out = io.StringIO()
    assert pipewrench.main(["realtime"], runner=run_ok(), out=out) == 0
    assert "PipeWire audio runs realtime" in out.getvalue()
