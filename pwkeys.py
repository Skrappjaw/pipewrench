"""Pure logic for PipeWrench's key bindings: which single key runs each shortcut, and what clashes.

The tab handlers in pwcore only know each action's default key. translate() maps the key Skrappjaw pressed onto
that default, so rebinding needs no change in the handlers. Bindings are kept in ui.json as overrides only.
"""
from collections import namedtuple

Action = namedtuple("Action", "screen id default label")

SCREENS = {"mixer": "Mixer", "devices": "Devices", "patch": "Patch", "settings": "Settings"}

# Letter shortcuts only. Arrows, Enter, Esc, Space and Tab never rebind. Order matters: in a clash the
# earlier action keeps the key.
ACTIONS = [
    Action("mixer", "mixer.meters", "v", "meters"),
    Action("devices", "devices.show_hidden", "h", "show hidden"),
    Action("devices", "devices.monitors", "m", "monitors"),
    Action("devices", "devices.add_bus", "a", "add bus"),
    Action("devices", "devices.delete_bus", "x", "delete bus"),
    Action("devices", "devices.test", "t", "test noise"),
    Action("devices", "devices.profile", "p", "card profile"),
    Action("patch", "patch.grid", "g", "grid/column view"),
    Action("patch", "patch.monitors", "m", "monitors"),
    Action("patch", "patch.ports", "p", "ports"),
]
BY_ID = {a.id: a for a in ACTIONS}

# The Global rows of Key Bindings: fixed keys, listed so a MIDI/CC binding can sit beside them later
# (Skrappjaw, 2026-10-26). (id, label, key as shown); never in bindings() or clean().
FIXED = [("global.next", "Next View", "Tab"), ("global.prev", "Prev View", "⇧Tab")] + [
    (f"global.tab{n}", label, str(n))
    for n, label in enumerate(["Mixer", "Devices", "Patch Bay", "Settings", "Top", "About"], 1)]
# Key Bindings headings, in order; Settings has no letter actions since save/restart went to its buttons.
HEADINGS = {"mixer": "Mixer", "devices": "Devices", "patch": "Patch Bay"}

# Keys that act on every tab; a binding to one of them never fires.
GLOBAL = {"q": "quit", **{str(n): f"tab {n}" for n in range(1, 7)},
          "*": "play/pause", "<": "previous track", ">": "next track",
          "-": "player volume", "+": "player volume", "=": "player volume"}


def label(a):
    return f"{SCREENS[a.screen]} · {a.label}"


def bindable(key):
    return isinstance(key, str) and len(key) == 1 and key.isprintable() and not key.isspace()


def clean(raw):
    """Overrides from ui.json: unknown actions and keys that can't be bound are dropped. None = unbound."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in BY_ID and (v is None or bindable(v))}


def bindings(overrides):
    """{action id: key or None}: the defaults with the overrides on top."""
    b = {a.id: a.default for a in ACTIONS}
    b.update(clean(overrides))
    return b


def overrides(b):
    """What ui.json keeps: the bindings that differ from the defaults."""
    return {a.id: b[a.id] for a in ACTIONS if b.get(a.id, a.default) != a.default}


def clashes(b):
    """{action id: reason} for every binding that collides with a global key or another action on its screen."""
    out = {}
    for a in ACTIONS:
        k = b.get(a.id)
        if k is None:
            continue
        if k in GLOBAL:
            out[a.id] = f"global: {GLOBAL[k]}"
            continue
        others = [o for o in ACTIONS if o.id != a.id and o.screen == a.screen and b.get(o.id) == k]
        if others:
            out[a.id] = "also " + ", ".join(label(o) for o in others)
    return out


def refuse(b, action_id, key):
    """Why key can't be bound to action_id, or None if it can."""
    if not bindable(key):
        return "only single keys can be bound"
    if key in GLOBAL:
        return f"{key} is global: {GLOBAL[key]}"
    a = BY_ID[action_id]
    other = next((o for o in ACTIONS if o.id != a.id and o.screen == a.screen and b.get(o.id) == key), None)
    return f"{key} is already {label(other)}" if other else None


def active(screen, b):
    """{pressed key: default key} for one screen. Global keys and the later side of a clash are left out."""
    m = {}
    for a in ACTIONS:
        k = b.get(a.id)
        if a.screen == screen and k is not None and k not in GLOBAL and k not in m:
            m[k] = a.default
    return m


def translate(screen, key, b):
    """The key the tab handler should see: a bound key becomes its action's default; a default key that no
    longer runs its action gives None; anything else passes through."""
    m = active(screen, b)
    if key in m:
        return m[key]
    if any(a.screen == screen and a.default == key for a in ACTIONS):
        return None
    return key


def key_for(b, action_id):
    """The key shown for an action in the legend, or None when it's unbound or its binding can't fire."""
    a, k = BY_ID[action_id], b.get(action_id)
    return k if k is not None and active(a.screen, b).get(k) == a.default else None
