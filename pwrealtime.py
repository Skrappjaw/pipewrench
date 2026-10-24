"""Pure logic for the realtime check: does PipeWire's audio thread run realtime, and if not, what fixes it.
Reads a /proc tree and limits files passed in; the one command (journalctl) goes through a runner.
Background: docs/pipewire-realtime.md in Skrappjaw's vault. pwrench shows fixes; it never runs sudo.
"""
import grp
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROCS = ("pipewire", "pipewire-pulse")
LOOP = "pw-data-loop"
LIMITS = [Path("/etc/security/limits.conf"), Path("/etc/security/limits.d")]
JOURNAL_CMD = ["journalctl", "--user", "--since", "-10min", "--no-pager", "-o", "cat"]
RESTART = "systemctl --user restart pipewire pipewire-pulse wireplumber"


@dataclass(frozen=True)
class Thread:
    tid: int
    policy: int               # 0 normal, 1 FIFO, 2 RR
    prio: int

    @property
    def realtime(self):
        return self.policy in (1, 2)


@dataclass(frozen=True)
class Proc:
    pid: int
    comm: str
    ppid: int                 # the user's systemd manager, which PipeWire inherits its limits from
    limit: int                # Max realtime priority (soft)
    loops: tuple = ()


@dataclass(frozen=True)
class Status:
    user: str
    groups: dict              # group -> rtprio granted by the limits files
    member: bool              # the user is in one of those groups (in /etc/group)
    procs: tuple = ()
    refusals: int = None      # portal "Realtime error" lines in the last 10 min; None if unknown
    group: str = "pipewire"   # the group the fix names
    limit_wanted: int = field(default=95)

    @property
    def threads(self):
        return [t for p in self.procs for t in p.loops]

    @property
    def limit(self):
        return min((p.limit for p in self.procs), default=None)

    @property
    def ok(self):
        return bool(self.procs) and self.member and self.limit > 0 and all(t.realtime for t in self.threads)


def limits_groups(paths=LIMITS):
    """Groups given rtprio > 0 by limits.conf and limits.d/*.conf (a .disabled file doesn't count)."""
    files = []
    for p in map(Path, paths):
        files += sorted(p.glob("*.conf")) if p.is_dir() else [p] if p.is_file() else []
    groups = {}
    for f in files:
        for line in f.read_text(errors="replace").splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) == 4 and parts[0].startswith("@") and parts[2] == "rtprio":
                try:
                    n = int(parts[3])
                except ValueError:
                    continue
                if n > 0:
                    groups[parts[0][1:]] = n
    return groups


def _stat(path):
    """(ppid, rt_priority, policy) from a stat file; comm may hold spaces, so split after the last ')'."""
    rest = path.read_text().rsplit(")", 1)[1].split()
    return int(rest[1]), int(rest[37]), int(rest[38])


def _limit(path):
    m = re.search(r"Max realtime priority\s+(\d+|unlimited)", path.read_text())
    return 0 if not m else 99 if m.group(1) == "unlimited" else int(m.group(1))


def pipewire_procs(proc=Path("/proc")):
    found = []
    for d in sorted(Path(proc).iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else -1):
        if not d.name.isdigit():
            continue
        try:
            comm = (d / "comm").read_text().strip()
            if comm not in PROCS:
                continue
            ppid = _stat(d / "stat")[0]
            loops = []
            for t in sorted((d / "task").iterdir(), key=lambda p: int(p.name)):
                if (t / "comm").read_text().strip() == LOOP:
                    _, prio, policy = _stat(t / "stat")
                    loops.append(Thread(int(t.name), policy, prio))
            found.append(Proc(int(d.name), comm, ppid, _limit(d / "limits"), tuple(loops)))
        except (OSError, ValueError, IndexError):
            continue  # a process that exited mid-read
    return found


def portal_refusals(run):
    try:
        rc, out = run(JOURNAL_CMD)
    except (OSError, ValueError):
        return None
    return out.count("Realtime error") if rc == 0 else None


def _members(group):
    try:
        g = grp.getgrnam(group)
    except KeyError:
        return []
    return list(g.gr_mem)


def check(proc=Path("/proc"), limits=LIMITS, user=None, members=_members, run=None):
    user = user or os.environ.get("USER") or os.getlogin()
    groups = limits_groups(limits)
    group = "pipewire" if "pipewire" in groups or not groups else sorted(groups)[0]
    member = any(user in members(g) for g in groups)
    return Status(user, groups, member, tuple(pipewire_procs(proc)),
                  portal_refusals(run) if run else None, group, groups.get(group, 95))


def rows(s):
    """(label, mark, text) for the Settings section and the CLI; mark is ● ok, ◐ problem, "" info."""
    out = [("pipewire group" if s.group == "pipewire" else f"{s.group} group",
            "●" if s.member else "◐",
            f"{s.user} is a member" if s.member else f"{s.user} is not a member")]
    if not s.procs:
        out.append(("realtime limit", "◐", "PipeWire isn't running"))
    else:
        lim = s.limit
        note = " (PipeWire started before the group change)" if lim == 0 and s.member else ""
        out.append(("realtime limit", "●" if lim > 0 else "◐", f"{lim}{note}"))
        bad = [t for t in s.threads if not t.realtime]
        if not s.threads:
            out.append(("audio threads", "◐", "none found"))
        elif bad:
            out.append(("audio threads", "◐", f"{len(bad)} of {len(s.threads)} not realtime"))
        else:
            prios = sorted({t.prio for t in s.threads})
            out.append(("audio threads", "●", f"{len(s.threads)} realtime, priority {'/'.join(map(str, prios))}"))
    n = "?" if s.refusals is None else s.refusals
    out.append(("portal refusals", "", f"{n} in the last 10 min (apps asking the desktop portal)"))
    return out


def fix(s):
    """What to do, first rule that applies. Commands are for Skrappjaw to run in a terminal."""
    if s.ok:
        return ["PipeWire audio runs realtime"]
    if not s.groups:
        return ["no file in /etc/security/limits.d grants realtime priority to a group"]
    if not s.member:
        return [f"sudo usermod -aG {s.group} {s.user}, then reboot"]
    if not s.procs:
        return [f"start PipeWire: {RESTART}"]
    if s.limit == 0:
        mgr = s.procs[0].ppid
        return ["reboot so PipeWire starts with the new limits (logging out may not restart it)",
                f"for now (drops calls): sudo prlimit --pid {mgr} --rtprio={s.limit_wanted}:{s.limit_wanted}"
                f" && {RESTART}"]
    return [f"restart PipeWire (drops calls): {RESTART}"]
