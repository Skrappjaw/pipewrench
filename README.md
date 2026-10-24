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

## The four views

Switch views with `Tab`, the number keys `1`–`4`, or a click on the tab bar. `q` quits from any view.
The bottom line shows that view's keys, and gives way to status messages.

### 1 Settings

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

| Key | Mouse | Action |
|---|---|---|
| ↑ ↓ | click a row | select a setting |
| ← → | click ◀ ▶, or the wheel over a row | step the value (pending) |
| Enter | [Apply], or double-click a row | write all pending values |
| Esc | [Revert] | drop pending values |
| R | [Restart WirePlumber] | restart WirePlumber, after y/n |

### 2 Top

A live `pw-top -b` view: state, ID, quantum, rate, WAIT, BUSY, W/Q, B/Q, ERR, format and name.
Followers are shown with a `+` under their driver. Rows with errors show in red, idle rows dim.

| Key | Mouse | Action |
|---|---|---|
| ↑ ↓, PgUp PgDn | wheel | scroll |

### 3 Presets

Named, labelled snapshots of the Settings rows (force rate, force buffer, min, max, and optionally
log level). They're stored in `~/.config/pwctl/presets.json`.

| Key | Mouse | Action |
|---|---|---|
| ↑ ↓ | click a row | select a preset |
| Enter | [Apply], or double-click | write the preset's values (ordered as in Settings) |
| s | [Save current] | save the current settings; asks for a name, then a label |
| e | [Edit label] | relabel the selected preset |
| d | [Delete] | delete it, after y/n |

Preset names are 1–32 letters, digits, `-` or `_`.

### 4 Devices

An inventory of every audio card, sink, source and bus on the system, read from `pw-dump`.

- **Layout:** hardware cards first (in name order), each with its active profile. Then
  **Virtual**: buses and other null sinks, and loopbacks.
  - Hardware means a node with a device API (`alsa`, `bluez5`); everything else is virtual.
  - A card whose profile is `off` still shows, so you can switch it on.
- **Each row shows:**
  - ★ on the default output and input
  - direction: out / in / duplex
  - label, state, volume (wpctl's scale, so 100% is unity) and node id
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

Two kinds of keys:

| Act at once | |
|---|---|
| d | make the selected device the default output or input |
| ← → | volume −5% / +5% |
| Space | mute toggle |
| p | switch the card's profile (prompts with the list; on a card heading or any of its rows) |
| t | play a 1-second quiet noise burst on an output |

| Edit `devices.json` (applied with A) | |
|---|---|
| Enter | label the device (empty clears). Apps show the label; the node name never changes, so they keep their picks |
| h | hide or unhide the row or card (pwctl only, saved at once) |
| a / x | add a bus / delete a bus that's in `devices.json` |
| v | preview the generated files in `$PAGER` |
| A | write the generated files and restart, after y/n |

View toggles: **H** shows hidden rows (dimmed), **m** adds a "(monitor)" input per output, as
capture apps such as OBS see them.

**Apply restarts only what it must:**
- **WirePlumber**, when only labels changed
- **PipeWire**, when buses or the clock changed

After the restart it checks that every bus exists and every label shows.

Mouse:
- click selects; double-click edits the label; the wheel moves the selection
- buttons for Default, Mute, Label, Profile, Add bus and Apply

The key legend is always on screen.

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
pwctl devices bus add NAME [LABEL] | bus delete NAME
pwctl devices preview                diff of the generated files against what's on disk
pwctl devices apply                  show the diff, ask, write, restart what's needed, check
pwctl devices rollback               ask, restore the previous files (or remove them), restart
```

DEV is a node id, an exact `node.name`, or a label that matches exactly one device. CARD is a card id,
name or label.

Exit codes: 0 ok; 1 a PipeWire command failed, a check found problems or you answered no;
2 bad input.

## Files

| Path | Written by | What |
|---|---|---|
| `~/.config/pwctl/presets.json` | Presets view, `preset save/delete` | presets |
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
  `object.linger`, created by PipeWire at start-up from the drop-in. Its node name is the bus name.
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
| `XDG_CONFIG_HOME` | base for both config files and the generated files |
| `PWCTL_FIXTURES` | read from recorded output in that folder and drop all writes (demo and tests) |
| `PAGER` | pager for the Devices preview |

## Code map

| File | Role |
|---|---|
| `pwctl.py` | entry point: CLI dispatch, the subprocess runner, the curses `Tui` |
| `pwcore.py` | pure logic: settings metadata, `pw-top` parsing, presets, the UI state machine and screen rendering |
| `pwinventory.py` | pure logic: the device inventory from `pw-dump`, lookups, wpctl command builders |
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
- **Commands:**
  - `default_cmd(row)`, `mute_cmd(row)`
  - `volume_cmd(row, step)`: `+5%`, `-5%`, `50%` or `0.5`, up to 150%
  - `profile_cmd(card, profile)`: by index or name

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
| `test_devices_ui.py` | Devices tab keys, mouse, rendering, scrolling, key legend |
| `test_tui_smoke.py` | the real curses UI in a pty against fixtures |
| `test_live.py` | opt-in; writes back only the values already set |

The design is in `docs/devices-v2-plan.md`; current state is in `HANDOFF.md`.
