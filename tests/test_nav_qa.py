"""Navigation QA sweep (Skrappjaw, 2026-10-25: "include QA as part of the testing to find potential UI
navigation issues"). Seeded random walks over every tab, by key and by mouse, with invariants checked
after every step. Actions are recorded, never run. A failure is shrunk to its shortest step list."""
import json
import random
from pathlib import Path

import pytest

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi
import pwpatch as pp

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
DUMP = json.loads((FIX / "pwdump_patch.json").read_text())
DEV = pc.devices_view(pi.inventory(DUMP), pcfg.default_config())
PATCH = pp.graph(DUMP)
MX = pc.mixer_view(pi.inventory(DUMP), pcfg.default_config(), pi.streams(DUMP))
KEYS = (["up", "down", "left", "right", "enter", "esc", " ", "tab", "btab", "pgup", "pgdn", "backspace"]
        + [str(n) for n in range(1, len(pc.TABS) + 1)] + list("vgpmhsaxyntR*<>-+"))
WIDTHS = (40, 80, 110)
PREFS = ("theme", "meter_fps", "meter_fall", "peak_hold", "volume_bar")


class Broken(AssertionError):
    pass


def check(ok, what):
    if not ok:
        raise Broken(what)


def press(st, k):
    """(state, the state before dev_focus resolved): the Tui opens a Card profile jump's menu after the key."""
    raw, _ = pc.handle_key(st, k, CUR, DEV, PATCH, MX)
    return pc.focus_device(raw, DEV), raw


def render(st, width):
    return pc.render_screen(st, CUR, None, width, 40, DEV, PATCH, MX)


def is_reset(st, opened=False):
    """opened: a double-click on a category resets it, then opens its pane, as Enter would."""
    return (not st.pending and not st.editing and st.key_capture is None and st.mix_menu is None
            and not st.patch_menu and not st.patch_ports and (opened or st.settings_focus == "menu")
            and (st.dev_menu is None or st.dev_focus is not None))


def step_checks(before, k, after, width, via=None, raw=None):
    lines, _ = render(after, width)
    check(all(len(t) <= width for t, _ in lines), f"a line is wider than {width}")
    check(after.tab in pc.TABS, "unknown tab")
    if after.tab == "settings":
        top = pc.CLOCK_BUTTONS_ROW if after.settings_cat == "audio" else pc.RESET_KEYS_ROW
        check(0 <= after.cursor <= top, f"Settings cursor {after.cursor} outside 0..{top}")
    if after.mix_menu:
        check(0 <= after.mix_menu_cursor < max(1, len(pc.mix_menu_items(MX, after))), "Mixer menu cursor")
    if after.dev_menu:
        check(0 <= after.dev_menu_cursor < max(1, len(pc.dev_menu_items(DEV, after))), "Devices menu cursor")
    check(0 <= after.mix_cursor < max(1, len(pc.mixer_selectable(MX))), "Mixer cursor")
    check(0 <= after.dev_cursor < max(1, len(pc.dev_selectable(DEV, after))), "Devices cursor")
    if before.popup and k is not None:  # a popover stays until Enter or Esc (Skrappjaw, 2026-10-26)
        if k in ("enter", "esc"):
            check(after.popup != before.popup, "Enter or Esc left a popover up")
        elif k not in ("left", "right"):
            check(after.popup == before.popup and after.tab == before.tab, "a stray key closed a popover")
    left_view = after.tab != before.tab or after.settings_cat != before.settings_cat
    if left_view and not before.popup and not before.key_capture:
        check(is_reset(raw or after, opened=via == ("double", "settings_cat")),
              f"left {before.tab}/{before.settings_cat} without a reset")
    stayed = after.tab == "settings" and after.settings_focus == "pane"  # ← may leave, and leaving drops edits
    if editing_guard(before) and k in ("left", "right") and stayed:
        check(after.pending == before.pending, "←→ changed a clock value without Enter")
        check(all(getattr(after, p) == getattr(before, p) for p in PREFS), "←→ changed a preference without Enter")
    if after.editing:
        check(("Esc", "cancel") in pc.legend(after), "editing without the cancel hint")


def editing_guard(st):
    """A Settings value row with its value closed: ←→ must not change it."""
    if st.tab != "settings" or st.settings_focus != "pane" or st.editing or st.popup or st.key_capture:
        return False
    return st.cursor < pc.CLOCK_BUTTONS_ROW if st.settings_cat == "audio" else st.cursor in pc.PREF_ROWS


def no_trap(st):
    """From anywhere: at most three Esc, then Tab reaches the next tab, and Shift+Tab comes back."""
    for _ in range(3):
        st, _ = press(st, "esc")
    start = st.tab
    st, _ = press(st, "tab")
    check(st.tab == pc.TABS[(pc.TABS.index(start) + 1) % len(pc.TABS)], f"Tab from {start} went to {st.tab}")
    st, _ = press(st, "btab")
    check(st.tab == start, f"Shift+Tab didn't come back to {start}")


def walk(seed, n):
    """The step list of one walk: ("key", k) or ("mouse", pick, event), decided before running."""
    rnd = random.Random(seed)
    steps = []
    for _ in range(n):
        r = rnd.random()
        if r < 0.15:
            steps.append(("mouse", rnd.random(), rnd.choice(["click", "click", "double"])))
        elif r < 0.35:
            steps.append(("chip", rnd.random()))
        else:
            steps.append(("key", rnd.choice(KEYS)))
    return steps


def run(steps, start_tab, key_box=True):
    """None if every step holds, else (index, message)."""
    st = pc.UIState(tab=start_tab, key_box=key_box)
    for i, step in enumerate(steps):
        width = WIDTHS[i % len(WIDTHS)]
        before = st
        try:
            via, raw = None, None
            if step[0] == "key":
                k = step[1]
                st, raw = press(st, k)
            elif step[0] == "chip":
                keys = [pc._chip_key(label) for label, _ in pc.legend(st)] if st.key_box else KEYS  # no box: no chips
                keys = [k for k in keys if k]
                k = keys[int(step[1] * len(keys))] if keys else "esc"
                st, raw = press(st, k)
            else:
                k = None
                _, hits = render(st, width)
                if hits:
                    row, c0, c1, target = hits[int(step[1] * len(hits))]
                    via = (step[2], target[0])
                    raw, _ = pc.handle_mouse(st, hits, row, c0, step[2], CUR, DEV, PATCH, MX)
                    st = pc.focus_device(raw, DEV)
            step_checks(before, k, st, width, via, raw)
            if i % 40 == 39:
                no_trap(st)
        except Broken as e:
            return i, str(e)
        except Exception as e:  # a crash is a finding too
            return i, f"{type(e).__name__}: {e}"
    return None


def shrink(steps, start_tab, key_box=True):
    """Drop steps one at a time while the walk still fails, for a short reproduction."""
    found = run(steps, start_tab, key_box)
    steps = steps[:found[0] + 1]
    i = 0
    while i < len(steps):
        trial = steps[:i] + steps[i + 1:]
        if trial and run(trial, start_tab, key_box):
            steps = trial
        else:
            i += 1
    return steps, run(steps, start_tab, key_box)


@pytest.mark.parametrize("start_tab", pc.TABS)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_navigation_keeps_every_invariant(seed, start_tab):
    steps = walk(seed * 100 + pc.TABS.index(start_tab), 400)
    if run(steps, start_tab):
        short, (at, why) = shrink(steps, start_tab)
        pytest.fail(f"{why} after {len(short)} steps from {start_tab}: {short}")


@pytest.mark.parametrize("start_tab", pc.TABS)
def test_random_navigation_with_the_keys_box_hidden(start_tab):
    # Skrappjaw, 2026-10-26: with the Keys box hidden, nothing traps the walk and every invariant holds.
    steps = walk(700 + pc.TABS.index(start_tab), 400)
    if run(steps, start_tab, key_box=False):
        short, (at, why) = shrink(steps, start_tab, key_box=False)
        pytest.fail(f"{why} after {len(short)} steps from {start_tab} (Keys box hidden): {short}")
