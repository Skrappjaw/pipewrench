# pwctl

A terminal replacement for PipeControl, for PipeWire on Linux Mint. It changes the sample rate and
buffer size live, shows `pw-top`, keeps labelled presets, and lists every audio device with
defaults, volume, card profiles, labels and persistent buses.

Standard library Python only. The UI is curses, with keyboard and mouse.

## Install and launch

```
ln -s "$PWD/pwctl.py" ~/.local/bin/pwctl      # from the repo root
pwctl                                          # interactive UI
pwctl status                                   # one-shot commands, see below
```

On this machine, `pwctl` → `projects/pwctl/pwctl.py` (branch `main`).

Needs PipeWire's command-line tools: `pw-metadata`, `pw-top`, `pw-dump`, `pw-cat`, plus `systemctl --user`.

## The five views

**Basic** (everyday audio): Mixer, Devices. **Advanced** (engineering): Patch, Settings (clock and
presets), Top. pwctl opens on the Mixer.

A **now-playing bar** sits above the keys on every tab. It shows the media player that's playing (MPRIS,
via `busctl`): `*` play/pause, `-` `+` its volume (numpad keys), `<` `>` previous/next. There's no stop: Brave
can't be restarted from outside once stopped. Notes:
`docs/media-bar-plan.md`.

Switch views with `Tab`, the number keys `1`–`5`, or a click on the tab bar. `q` quits from any view.
Every view lists its keys the same way: a boxed legend at the bottom, each key a bold inverted chip with
what it does beside it, starting with Enter wherever Enter acts. The line under the box carries
messages and y/n questions.

### 1 Mixer

Everyday audio in one place: every output and input (hidden rows and monitors left out), then the apps
playing or recording. Each row has a volume bar: green to 100%, red past it (up to 150%), dim when
muted. ★ marks the default playback and recording device. Apps show what they're connected to
(`Discord → GCS-8 CH 4-AUX`); JACK clients like REAPER have no volume control here.

| Key | Mouse | Action |
|---|---|---|
| ↑ ↓ | click a row | select |
| ← → | the wheel over a row | volume −5% / +5% |
| Space | its chip | mute or unmute |
| Enter, d | double-click a device | make it the default |
| v | its chip | live meters on or off |

Volume, mute and the default act at once (`wpctl`). Running rows and the selected one show a live peak
meter (green, yellow past −18 dB, red past −6 dB); a suspended device has no audio and isn't woken just
to meter it. Design notes and CPU figures: `docs/mixer-plan.md`.

### 2 Devices

An inventory of every audio card, sink, source and bus on the system, read from `pw-dump`.

- **Layout:** hardware cards first (in name order), each with its active profile. Then
  **Virtual**: buses and other null sinks, and loopbacks.
  - Hardware means a node with a device API (`alsa`, `bluez5`); everything else is virtual.
  - A card whose profile is `off` still shows, so you can switch it on.
- **Each row shows:**
  - ★ on the default output and input
  - direction: out / in / duplex
  - label, state, volume (wpctl's scale, so 100% is unity) and node id
  - connections, from PipeWire's links:
    - `← Brave`: what plays into the device
    - `→ OBS`: what records from it; on an output that's its monitor, marked `(monitor)`
    - qpwgraph patches between devices show the other device's label
- **Tags:**
  - `pwctl`: the bus comes from pwctl's drop-in
  - `made outside pwctl`: a live bus with the same name as one in `devices.json`, e.g. one made by a
    script; applying would make a second
  - `hidden`
  - `label → …`: a label saved but not yet applied
- **Pending buses** (in `devices.json`, not running yet) are listed at the end.
- **Order within a card:** outputs, then inputs, each in natural `node.name` order. A split
  multichannel interface such as the BOSS GCS-8 (see github.com/Skrappjaw/gcs-8-linux) therefore
  reads in channel order.

**Everything starts with Enter.** Select something with ↑↓, press Enter, and pick from its menu with
↑↓ and Enter. Esc or ← closes the menu, and so does any other key.

| Selected | Its menu |
|---|---|
| a device | Rename…, Make default, Mute/Unmute, Test sound (outputs), Hide |
| a bus | the same, plus Delete bus (after y/n) |
| a pending bus | Create now, Rename…, Delete bus |
| a card heading | its profiles (● is active; picking one asks y/n, since it interrupts that card), Hide card |
| + Add bus… | asks for a name and creates it live |

- **Rename** saves the label and writes the label file, then asks "Restart WirePlumber now to show it?"
  An empty name restores the default.
  (y/n), because labels only appear after WirePlumber reloads. A bus shows its new name after the next
  login.
- **Without opening a menu:** ←→ change the volume.
- **Shortcuts for the menu items:**
  - `d` make default, `Space` mute, `t` test
  - `p` the card's profile menu
  - `h` hide, `a` add bus, `x` delete bus
  - `v` preview, `A` write files, `R` restart WirePlumber

View toggles: **H** shows hidden rows (dimmed), **m** adds a "(monitor)" input per output, as
capture apps such as OBS see them.

**Nothing restarts unless you ask.** A restart drops app connections, and some apps (Discord,
for one) don't reconnect. So:
- **Buses** are created and removed live, with no restart.
- **Labels and the clock setting** are written by A and show after the next restart: at login, with
  R (WirePlumber), or with `pwctl devices apply --restart`.
- Relabelling a running bus also waits for a restart, because changing it live would mean
  re-creating it.

Mouse:
- click selects; double-click edits the label; the wheel moves the selection
- click a key chip in the legend to press that key

The key legend is always on screen.

### 3 Patch

A patch bay for live connections, in two layouts switched with `g`:
- **Columns:** sources on the left, destinations on the right; ● marks where the selected source goes.
- **Grid:** sources down the side, numbered destinations across the top (names in a key below):
  - ● connected
  - ◐ one channel only
  - · not connected

How it works:
- **Sources:** app playback, device inputs and buses.
- **Destinations:** app recording, device outputs and buses.
- **Connecting** links matching channels (FL→FL, FR→FR); a mono source feeds both channels.
- **Ports:** `p` opens a port matrix for the selected pair: source ports down the side,
  destination ports across the top. Use it for fine patches, e.g. CH 2's FL into REAPER in3.
- **Monitors:** outputs' monitors are hidden until `m`.
- **JACK apps** (e.g. REAPER via `pw-jack`) appear as apps. Their ports are named `out1`, `in1`…, so
  they pair by position (out1/out2 → FL/FR, FL/FR → in1/in2); `p` reaches the other inputs.

| Key | Action |
|---|---|
| ↑ ↓ / ← → | columns: select / switch column. Grid: source / destination |
| Enter | connect or disconnect the selected pair (on a source in columns: move to the destinations). On unnamed inputs (REAPER's in1…in8) it opens a slot menu: In 1-2, In 3-4… with who's on each, cursor on the next free one |
| → (columns) | move to the destinations, landing on one the source already feeds |
| p | port matrix for the chosen source and destination (on the source side it moves you to the destinations first): ↑↓ source port, ←→ destination port, Enter links/unlinks; `p`/Esc back |
| g | switch between columns and grid |
| m | show or hide output monitors |

Mouse:
- click selects; double-click connects or disconnects
- in the grid, click a cell

Links made here last until a restart or until the app reconnects. Saved patch sets are planned
as part of scenes.

### 4 Settings

PipeWire's live clock settings, read from the `settings` metadata (`pw-metadata -n settings 0`).

| Row | Metadata key | Choices |
|---|---|---|
| Force sample rate | `clock.force-rate` | do not force, 44100, 48000, 88200, 96000, 176400, 192000 |
| Minimum buffer | `clock.min-quantum` | 8 … 8192 (powers of two) |
| Max buffer | `clock.max-quantum` | 8 … 8192 |
| Force buffer size | `clock.force-quantum` | do not force, 8 … 8192 |
| Log level | `log.level` | No log, Error, Warning, Info, Debug, Trace |

- Changing a value only marks it pending, shown with `*`. Nothing is written until you apply.
- On Apply, writes are ordered so min never exceeds max at any step, and the force values go last.
- A min above max blocks the apply. A forced buffer outside min/max, or a rate outside
  `clock.allowed-rates`, applies with a warning.
- Below the rows: the allowed rates and the defaults (`clock.rate`, `clock.quantum`).
- These settings are runtime only. A PipeWire restart clears them.
- **Meter frame rate** (Display): 5–60 fps for the Mixer's meters; default 10. 60 fps costs pwctl about
  22% CPU with 11 meters, against about 9% at 10 fps.
- **Meter fall** (Display): 10, 20, 40 or 80 dB a second after a peak; default 20.
- **Peak hold** (Display): off, 0.5, 1, 1.5, 2 or 3 s; default 1.5. A `▌` marker hangs at the last peak,
  then falls 20 dB a second.
- Both are saved with the theme in `~/.config/pwctl/ui.json`.
- **Theme** (the Display section) is pwctl's own: dark, light, or crowbar (orange, after Mint-Y-Dark-Orange; needs 256 colours). ←→ switches it at once, and it's kept in
  `~/.config/pwctl/ui.json`. The colour roles are described in `docs/ui-conventions.md`.

| Key | Mouse | Action |
|---|---|---|
| ↑ ↓ | click a row | select a setting |
| ← → | click ◀ ▶, or the wheel over a row | step the value (pending) |
| Enter | its chip, or double-click a row | write all pending values |
| Esc | its chip | drop pending values |
| R | its chip | restart WirePlumber, after y/n |

#### Presets (the bottom of Settings)

Named, labelled snapshots of the Settings rows (force rate, force buffer, min, max, and optionally
log level). They're stored in `~/.config/pwctl/presets.json`.

| Key | Mouse | Action |
|---|---|---|
| ↑ ↓ | click a row | select a preset |
| Enter | its chip, or double-click | write the preset's values (ordered as in Settings) |
| s | its chip | save the current settings; asks for a name, then a label |
| e | its chip | relabel the selected preset |
| d | its chip | delete it, after y/n |

Preset names are 1–32 letters, digits, `-` or `_`.

### 5 Top

A live `pw-top -b` view: state, ID, quantum, rate, WAIT, BUSY, W/Q, B/Q, ERR, format and name.
Followers are shown with a `+` under their driver. Rows with errors show in red, idle rows dim.

| Key | Mouse | Action |
|---|---|---|
| ↑ ↓, PgUp PgDn | wheel | scroll |

## Command line

```
pwctl status                         current settings, allowed rates, defaults
pwctl rate N | buf N | min N | max N | log N
                                     set one value now (rate/buf 0 = do not force)
pwctl reset                          stop forcing rate and buffer
pwctl top                            run pw-top
pwctl preset list
pwctl preset save NAME [LABEL]       save the current settings
pwctl preset apply NAME
pwctl preset delete NAME

pwctl devices [list] [--hidden] [--monitors]   every card, device and bus
pwctl devices default DEV            make DEV the default output or input
pwctl devices volume DEV STEP        +5%, -5%, 50% or 0.5
pwctl devices mute DEV               toggle
pwctl devices profile CARD PROFILE   by index or name, e.g. `profile GCS-8 HiFi` (asks first)
pwctl devices test DEV               noise burst on an output, to find it by ear
pwctl devices label DEV TEXT | label DEV --clear
pwctl devices hide NAME | unhide NAME   a device or a whole card, in pwctl only
pwctl devices bus add NAME [LABEL]   create a bus live, and keep it for every login
pwctl devices bus delete NAME       remove a virtual bus live, and forget it
pwctl devices preview                diff of the generated files against what's on disk
pwctl devices apply [--restart]      write the generated files; --restart asks, restarts, then checks
pwctl devices rollback               restore the previous files (or remove them); no restart
pwctl devices restart                restart WirePlumber, after y/n

pwctl patch [list]                   every live connection, with its channels
pwctl patch connect SRC DST          link matching channels (mono feeds both)
pwctl patch disconnect SRC DST       remove every link between them
```

DEV is a node id, an exact `node.name`, or a label that matches exactly one device. CARD is a card id,
name or label.

Exit codes: 0 ok; 1 a PipeWire command failed, a check found problems or you answered no;
2 bad input.

## Files

| Path | Written by | What |
|---|---|---|
| `~/.config/pwctl/presets.json` | Presets view, `preset save/delete` | presets |
| `~/.config/pwctl/ui.json` | Settings → Theme | display preferences (theme) |
| `~/.config/pwctl/devices.json` | Devices view, `devices label/hide/bus` | buses, labels, hidden rows, clock: the source of truth |
| `~/.config/pipewire/pipewire.conf.d/50-pwctl-devices.conf` | `devices apply` | the buses and clock, as a PipeWire drop-in |
| `~/.config/wireplumber/main.lua.d/51-pwctl-labels.lua` | `devices apply` | labels as WirePlumber 0.4 rules |
| `~/.config/wireplumber/wireplumber.conf.d/51-pwctl-labels.conf` | `devices apply` | the same on WirePlumber 0.5+ |
| `….bak` next to each generated file | `devices apply` | the previous version, used by rollback |

- Both generated files start with a "Generated by pwctl" header. pwctl refuses to overwrite or roll
  back a file without it.
- A generated file is only created when there's something to put in it.

`devices.json`:

```json
{
  "version": 2,
  "clock_rate": 48000,
  "buses": [{"name": "OBS", "label": "OBS feed"}],
  "labels": {"alsa_input.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_in_GCS8_0_6_7__source": "GCS-8 CH 2 (Mic)"},
  "hidden": ["alsa_card.usb-046d_HD_Pro_Webcam_C920_0123ABCD-02"]
}
```

- **Validation:**
  - bus names are 1–32 letters, digits, `-` or `_`
  - labels have no quotes, backslashes or newlines
  - `clock_rate` is one of the Settings rates, or `null` to leave the clock alone
- **Older files:** a version 1 file (from the GCS-8 loopback-pair design) loses its pairs on load and
  keeps its buses and clock.

## How the generated files work

- **Buses:** each one is a `support.null-audio-sink` with `media.class = Audio/Duplex` and
  `object.linger`. Its node name is the bus name.
  - pwctl creates it live with `pw-cli create-node`, and removes it with `pw-cli destroy`.
  - The drop-in recreates it at every PipeWire start.
- **Labels:** WirePlumber rules that set `node.description` for a matching `node.name` when the
  device appears.
  - ALSA devices use `alsa_monitor.rules` (0.4) or `monitor.alsa.rules` (0.5+).
  - Bluetooth devices use the bluez equivalents.
  - pwctl reads the version from `wireplumber --version`.

pwctl no longer makes devices of its own for a particular interface. Device-specific splits belong
in ALSA UCM profiles, which work for every app and distribution; the GCS-8 one is at
github.com/Skrappjaw/gcs-8-linux.

## Environment

| Variable | Effect |
|---|---|
| `PWCTL_PRESETS` | use this presets file instead of the default |
| `PWCTL_DEVICES` | use this devices file instead of the default |
| `PWCTL_UI` | use this display-preferences file instead of the default |
| `XDG_CONFIG_HOME` | base for both config files and the generated files |
| `PWCTL_FIXTURES` | read from recorded output in that folder and drop all writes (demo and tests) |
| `PAGER` | pager for the Devices preview |

## Code map

| File | Role |
|---|---|
| `pwctl.py` | entry point: CLI dispatch, the subprocess runner, the curses `Tui` |
| `pwcore.py` | pure logic: settings metadata, `pw-top` parsing, presets, the UI state machine and screen rendering |
| `pwinventory.py` | pure logic: the device inventory from `pw-dump`, lookups, wpctl command builders |
| `pwpatch.py` | pure logic: patch bay graph (sources, destinations, ports), channel pairing, pw-link commands |
| `pwconfig.py` | pure logic: `devices.json`, the generated drop-in and label rules, apply/rollback safety |

The pure modules never run commands. Callers pass a `runner(argv) -> (rc, output)`, which is how
the tests replace PipeWire.

### pwcore.py

- **Settings:**
  - `SETTINGS`, `setting(key)`: the five rows above
  - `display_value(s, value)`, `cycle(s, current, step)`: show a value; step it
  - `read_settings(run)`, `parse_settings(text)`: read and parse `pw-metadata`
  - `set_cmd(key, value)`: build the command that writes one key
  - `validate(current, changes)`: raises on impossible changes, returns warnings for adjusted ones
  - `ordered_changes(current, changes)`: write order that keeps min ≤ max throughout
- **pw-top:**
  - `TopParser.feed(line)` / `flush()`: turn `pw-top -b` output into `TopRow` blocks
- **Presets:**
  - `presets_path()`, `load_presets`, `save_presets`
  - `add_preset`, `delete_preset`, `relabel_preset`, `find_preset`
  - `capture_preset(current, name, label)`, `preset_cmds(p, current)`, `summarize_preset(p)`
- **UI:**
  - `UIState`: tab, cursors, pending values, confirm prompt, message
  - `handle_key(st, key, current, presets, devices)` → `(state, actions)`: no side effects; the
    `Tui` carries out the actions
  - `handle_mouse(st, hits, y, x, event, …)`: maps clicks and the wheel onto the same keys
  - `render_screen(st, current, block, presets, width, height, devices)` → `(lines, hits)`: lines
    with styles, plus a hit map for the mouse

### pwinventory.py

- **Reading:**
  - `read_dump(run)`
  - `inventory(dump)`: an `Inventory` of `Card`s (with `Profile`s and their `Row`s), `virtual`
    rows and the configured `defaults`. App streams are left out.
- **Lookup:**
  - `Inventory.find(key)`: a row by id, `node.name`, or a label that matches exactly one row
  - `Inventory.find_card(key)`
- **Display:**
  - `monitor_rows(rows)`: one "(monitor)" input per output
  - `view(inv, hidden, show_hidden, monitors)`: headings and rows, as the CLI lists them
  - `connections(row)`: "← apps playing into it  → apps reading from it"
- **Commands:**
  - `default_cmd(row)`, `mute_cmd(row)`
  - `volume_cmd(row, step)`: `+5%`, `-5%`, `50%` or `0.5`, up to 150%
  - `profile_cmd(card, profile)`: by index or name
  - `bus_create_cmd(name, label)`, `destroy_cmd(row)`: live buses via `pw-cli`
- `merge_dump(text)`: applies the update arrays `pw-dump` prints when the graph changes mid-read

### pwpatch.py

- `graph(dump)`: a `Graph` of source and destination `Endpoint`s, with their `Port`s and the links
- `Graph`:
  - `state(src, dst)`: full / partial / none
  - `targets(src)`
  - `find_source(key)`, `find_dest(key)`
- `pair_ports(src_ports, dst_ports)`: FL→FL, mono to all, all to mono, else by position
- commands:
  - `connect_cmds`, `disconnect_cmds`, `toggle_cmds`: `pw-link` commands, skipping links that exist
  - `port_pairs`, `port_toggle_cmd`: port-level detail

### pwconfig.py

- **devices.json:**
  - `default_config()`, `validate_config(c)`, `migrate(c)`
  - `config_path()`, `load_config`, `save_config`
- **Edits** (each returns a new, validated config):
  - `set_label(c, name, label)`
  - `add_bus`, `delete_bus`, `toggle_hidden`
- **Generated files:**
  - `render_dropin(c)`, `dropin_path()`
  - `render_labels(c, version)`, `labels_path(version)`, `wireplumber_version(run)`
- **Safety:**
  - `check_ours(path)`
  - `diff_generated(path, text)`
  - `write_generated(path, text)`: atomic, keeps `.bak`, returns False when unchanged
  - `rollback_generated(path)`
- **Checks:**
  - `bus_conflicts(c, live_names, applied_text)`: buses that exist live but aren't pwctl's
  - `check_applied(c, live)`: missing buses and labels not showing, after an apply
- **Restart commands:** `RESTART_PIPEWIRE`, `RESTART_WIREPLUMBER`

### pwctl.py

- `main(argv, runner, out, err, execvp, ask)`: no arguments starts the UI; otherwise runs a command.
  `ask` is the y/n prompt, which tests replace.
- `_devices(...)`: the `devices` subcommands.
- `_bus_add(...)`, `_bus_delete(...)`: live bus changes, shared by the CLI and the tab.
- `_generated(runner, c, version)`: the generated files that differ from disk, each with the
  restart it needs.
- `_settled_problems(c, runner)`: re-checks while PipeWire or WirePlumber comes back.
- `_play_noise(runner, row)`, `write_noise(path)`: the test burst.
- `Tui`: the curses loop, plus a background `pw-top` reader.
  - It refreshes every second; on the Devices tab it also re-reads `pw-dump` and `devices.json`.
  - It carries out the actions the state machine returns.

## Tests

```
python3 -m pytest -q                              # everything; no PipeWire calls
PWCTL_LIVE=1 python3 -m pytest tests/test_live.py # read-only checks against the real PipeWire
```

| File | Covers |
|---|---|
| `test_core.py`, `test_presets.py` | settings parsing, ordering, validation, presets |
| `test_ui.py` | Settings, Top and Presets keys, mouse and rendering |
| `test_cli.py` | settings and preset commands |
| `test_inventory.py` | cards, rows, grouping, order, defaults, volume, lookup, monitors, hidden (fixture: a trimmed live `pw-dump`) |
| `test_config.py` | `devices.json` v2, migration, edits, generated files (golden files in `tests/fixtures/golden/`), backups, rollback, checks |
| `test_commands.py` | wpctl command builders, bus clash check |
| `test_devices_cli.py` | `pwctl devices …` with a fake runner; restarts are recorded, never run |
| `test_patch.py`, `test_patch_cli.py`, `test_patch_ui.py` | patch graph, pairing, commands, CLI, both views, ports, mouse (fixture: a live `pw-dump` with ports and links) |
| `test_devices_ui.py` | Devices tab keys, mouse, rendering, scrolling, key legend |
| `test_tui_smoke.py` | the real curses UI in a pty against fixtures |
| `test_live.py` | opt-in; writes back only the values already set |

The design is in `docs/devices-v2-plan.md`; current state is in `HANDOFF.md`.
