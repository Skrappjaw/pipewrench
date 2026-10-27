"""Themes as TOML files: a palette of named colours, general roles, and specific roles that derive from them.

Built-ins live in themes/ beside this file; the user's in ~/.config/pipewrench/themes/. Each role is
(fg, bg, attr), where None is the terminal's own colour. No curses here. Guide: docs/themes.md.
"""
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_DIR = Path(__file__).parent / "themes"

# role: (layer, where it shows, what it inherits from). The order is the order export and the guide use.
ROLES = {
    "screen": ("general", "the whole screen: its background and default text", None),
    "normal": ("general", "ordinary text", None),
    "dim": ("general", "hints, secondary text, the empty part of bars", None),
    "selected": ("general", "the cursor bar: the row the keys act on", None),
    "anchor": ("general", "a row that's chosen but not active, and the current tab", None),
    "title": ("general", "section titles and headings", None),
    "ok": ("general", "values and things that worked", None),
    "pending": ("general", "unapplied changes and partial states", None),
    "alert": ("general", "errors and warnings", None),
    "chip": ("general", "key chips in the Keys box and the media bar", None),
    "bar_ok": ("general", "Mixer volume bar (block style), up to 100%", None),
    "bar_alert": ("general", "Mixer volume bar (block style), past 100%", None),
    "bar_dim": ("general", "Mixer volume bar (block style), muted or empty", None),
    "src": ("general", "Patch: source dots and outputs (optional)", "ok"),
    "dst": ("general", "Patch: destination dots and inputs (optional)", "ok"),
    "dst_partial": ("specific", "Patch: a destination that's partly connected", "pending"),
    "bar_src": ("specific", "Patch: the cursor in the Sources column", "selected"),
    "bar_dst": ("specific", "Patch: the cursor in the Destinations column", "selected"),
    "anchor_src": ("specific", "Patch: the chosen source while you pick a destination", "anchor"),
    "anchor_dst": ("specific", "Patch: the chosen destination", "anchor"),
    "head_play": ("specific", "Mixer: the Playback heading", "title"),
    "head_rec": ("specific", "Mixer: the Recording heading", "title"),
    "head_src": ("specific", "Patch: the Sources heading", "title"),
    "head_dst": ("specific", "Patch: the Destinations heading", "title"),
    "vol": ("specific", "Mixer: the volume bar up to 100%", "ok"),
    "vol_over": ("specific", "Mixer: the volume bar past 100%", "alert"),
    "dir_out": ("specific", "Devices: outputs", "dim"),
    "dir_in": ("specific", "Devices: inputs", "dim"),
    "meter_low": ("specific", "meters: normal level", "ok"),
    "meter_mid": ("specific", "meters: past −18 dB", "pending"),
    "meter_high": ("specific", "meters: past −6 dB", "alert"),
    "band_ok": ("specific", "the status band when something worked (✓)", "bar_ok"),
    "band_alert": ("specific", "the status band when something failed (✖)", "bar_alert"),
    "status_line": ("specific", "the status line: what the selected row is and does (a calm bar)", "anchor"),
    "popup": ("specific", "popover boxes", "chip"),
    "popup_sel": ("specific", "the chosen button in a popover", "selected"),
}
GENERAL = [r for r, (layer, *_) in ROLES.items() if layer == "general"]
SPECIFIC = [r for r, (layer, *_) in ROLES.items() if layer == "specific"]
_BARS = ("selected", "anchor", "bar_src", "anchor_src", "bar_dst", "anchor_dst")
LOGOS = ("wrench", "doom", "slant", "pipecraft", "computer", "ansi")
CURSES = {"black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"}
ATTRS = {"bold", "dim", "underline", "reverse"}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


class ThemeError(Exception):
    pass


@dataclass
class Library:
    themes: dict = field(default_factory=dict)   # id → {role: (fg, bg, attr)}, every role resolved
    names: list = field(default_factory=list)    # the Settings order: built-ins, then the user's by id
    user: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)     # id → {"name", "logo", "general", "specific"} after extends

    def label(self, name):
        return self.meta.get(name, {}).get("name") or name

    def logo(self, name):
        return self.meta.get(name, {}).get("logo") or "ansi"

    def min_colours(self, name):
        """256 when the theme uses exact hex, else the 8 basic colours."""
        return 256 if any(str(c).startswith("#") for v in self.themes[name].values() for c in v[:2]) else 8


def _colour(value, palette, where):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ThemeError(f"{where} must be text")
    value = palette.get(value, value)
    if _HEX.fullmatch(value) or value in CURSES or (value.startswith("bright") and value[6:] in CURSES):
        return value
    raise ThemeError(f'{where} "{value}" is not a colour (#rrggbb, a colour name, or a [palette] name)')


def _role(value, palette, where):
    if not isinstance(value, dict) or set(value) - {"fg", "bg", "attr"}:
        raise ThemeError(f"{where} must look like {{ fg = \"…\", bg = \"…\", attr = \"bold\" }}")
    attr = value.get("attr", "")
    if not isinstance(attr, str) or set(attr.split()) - ATTRS:
        raise ThemeError(f'{where}.attr "{attr}": use {", ".join(sorted(ATTRS))}')
    return (_colour(value.get("fg"), palette, f"{where}.fg"), _colour(value.get("bg"), palette, f"{where}.bg"), attr)


def _section(raw, name, allowed, palette):
    table = raw.get(name, {})
    if not isinstance(table, dict):
        raise ThemeError(f"[{name}] must be a table")
    out = {}
    for role, value in table.items():
        if role not in allowed:
            raise ThemeError(f"{name}.{role} is not a {name} role (see docs/themes.md)")
        out[role] = _role(value, palette, f"{name}.{role}")
    return out


def _lum(colour):
    """0 (black) to 1 (white) for a hex colour; None for a curses name or the terminal's default."""
    if not colour or not colour.startswith("#"):
        return None
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _louder_bands(t):
    """The ✓/✖ bands in bold (Skrappjaw, 2026-10-26: "a bold color highlight that is more obvious"), never bold on
    black: terminals draw bold black as grey, so black band text turns white first (Skrappjaw's pick). A dark hex colour
    goes through a 256-colour slot, which bold doesn't brighten, so it keeps its colour and gets bold."""
    for role in ("band_ok", "band_alert"):
        fg, bg, attr = t[role]
        if fg in ("black", "#000000"):
            if bg == "white" or (_lum(bg) or 0) > 0.6:
                continue  # light text on a light band would be unreadable; this one stays as it is
            fg = "white" if fg == "black" else "#ffffff"
        t[role] = (fg, bg, " ".join(dict.fromkeys(f"{attr} bold".split())))


def derive(general, specific):
    """Every role from the general ones plus explicit specific ones, in the order the old dicts used."""
    t = {**general, **specific}
    own = "src" in general  # a theme with its own in/out colours keeps its dots' colour on the bars
    t.setdefault("src", t["ok"])
    t.setdefault("dst", t["ok"])
    t.setdefault("dst_partial", t["pending"])
    for bar, base in (("bar_src", "selected"), ("bar_dst", "selected"), ("anchor_src", "anchor"),
                      ("anchor_dst", "anchor")):
        t.setdefault(bar, t[base])
    for dot in ("src", "dst", "dst_partial"):
        for bar in _BARS:
            t[f"{dot}@{bar}"] = (t[dot][0], t[bar][1], t[dot][2]) if own else t[bar]
    for bar in ("selected", "anchor"):  # a closed value row's grey ◀ ▶ on the cursor bar
        t[f"dim@{bar}"] = (t["dim"][0], t[bar][1], t["dim"][2])
    for role in SPECIFIC:
        t.setdefault(role, t[ROLES[role][2]])
    _louder_bands(t)
    if t["popup_sel"] == t["popup"]:  # pipeOS-dark: chip and selected match, so the choice was invisible
        fg, bg, _ = t["popup"]
        t["popup_sel"] = (bg, fg, "bold")
    return t


def _read(path):
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ThemeError(f"not valid TOML: {e}")
    except OSError as e:
        raise ThemeError(f"can't read it: {e}")


def load(dirs):
    """Every theme in these folders, built-ins first. A theme with any problem is left out whole."""
    lib, raws, builtin = Library(), {}, set()
    for n, d in enumerate(dirs):
        for path in sorted(Path(d).glob("*.toml")) if Path(d).is_dir() else []:
            tid, where = path.stem, f"themes/{path.name}"
            if n and tid in builtin:
                lib.problems.append(f'{where}: "{tid}" is a built-in theme; give yours another name and use '
                                    f'extends = "{tid}"')
                continue
            try:
                raws[tid] = (_read(path), where, n == 0)
            except ThemeError as e:
                lib.problems.append(f"{where}: {e}")
                continue
            if n == 0:
                builtin.add(tid)
    resolved = {}

    def resolve(tid, stack=()):
        if tid in resolved:
            return resolved[tid]
        raw, where, _ = raws[tid]
        try:
            for key in set(raw) - {"name", "extends", "logo", "order", "palette", "general", "specific"}:
                raise ThemeError(f"unknown key {key!r}")
            parent = raw.get("extends", None if tid == "dark" else "dark")
            if parent in stack + (tid,):
                raise ThemeError(f"extends loops: {' → '.join(stack + (tid, parent))}")
            if parent is not None and parent not in raws:
                raise ThemeError(f'extends "{parent}": no such theme')
            base = resolve(parent, stack + (tid,)) if parent else None
            if parent and base is None:
                raise ThemeError(f'extends "{parent}", which has a problem')
            palette = {**(base["palette"] if base else {}), **raw.get("palette", {})}
            if not all(isinstance(v, str) for v in palette.values()):
                raise ThemeError("[palette] values must be colours")
            logo = raw.get("logo", base["logo"] if base else "ansi")
            if logo not in LOGOS:
                raise ThemeError(f'logo "{logo}": use {", ".join(LOGOS)}')
            general = {**(base["general"] if base else {}), **_section(raw, "general", GENERAL, palette)}
            specific = {**(base["specific"] if base else {}), **_section(raw, "specific", SPECIFIC, palette)}
            missing = [r for r in GENERAL if r not in general and r not in ("src", "dst")]
            if missing:
                raise ThemeError(f"[general] is missing {', '.join(missing)}")
        except ThemeError as e:
            if not any(p.startswith(where) for p in lib.problems):
                lib.problems.append(f"{where}: {e}")
            resolved[tid] = None
            return None
        resolved[tid] = {"name": raw.get("name"), "logo": logo, "palette": palette, "general": general,
                         "specific": specific, "order": raw.get("order", 1000), "builtin": raws[tid][2]}
        return resolved[tid]

    for tid in raws:
        resolve(tid)
    ok = [t for t in raws if resolved.get(t)]
    lib.names = sorted(ok, key=lambda t: (not resolved[t]["builtin"], resolved[t]["order"], t))
    lib.user = [t for t in lib.names if not resolved[t]["builtin"]]
    for tid in lib.names:
        m = resolved[tid]
        lib.themes[tid] = derive(m["general"], m["specific"])
        lib.meta[tid] = {"name": m["name"], "logo": m["logo"], "general": m["general"], "specific": m["specific"]}
    return lib


def _inline(value):
    fg, bg, attr = value
    parts = [f'{k} = "{v}"' for k, v in (("fg", fg), ("bg", bg), ("attr", attr)) if v]
    return "{ " + ", ".join(parts) + " }" if parts else "{ }"


def export(lib, source, new_id):
    """A starting file for a new theme: the general roles written out, the specific ones as comments."""
    meta, theme = lib.meta[source], lib.themes[source]
    out = [f"# PipeWrench theme \"{new_id}\", made from {lib.label(source)}. Guide: docs/themes.md",
           "# Edit, save, then open Settings → Display → Theme and pick it.",
           f'name = "{new_id}"', f'extends = "{source}"', f'logo = "{meta["logo"]}"', "",
           "[palette]  # optional: name colours here and use the names below",
           '# accent = "#0a84ff"', "",
           "[general]  # the base roles; the specific ones follow them unless you set them"]
    out += [f"{role} = {_inline(meta['general'][role])}  # {ROLES[role][1]}" for role in GENERAL
            if role in meta["general"]]
    out += ["", "[specific]  # remove the # to set one element on its own"]
    out += [f"# {role} = {_inline(theme[role])}  # {ROLES[role][1]}; follows {ROLES[role][2]}" for role in SPECIFIC]
    return "\n".join(out) + "\n"


def role_table():
    """The role table in docs/themes.md, so the guide can't drift from the code."""
    rows = ["| Role | Layer | Where it shows | Follows |", "|---|---|---|---|"]
    rows += [f"| `{r}` | {layer} | {where} | {f'`{base}`' if base else '—'} |"
             for r, (layer, where, base) in ROLES.items()]
    return "\n".join(rows)
