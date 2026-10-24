# pwctl

A terminal replacement for PipeControl, for PipeWire on Linux Mint. It changes the sample rate and
buffer size live, shows `pw-top`, keeps labelled presets, and splits the
BOSS GCS-8 into named stereo devices that every app can pick.

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

Splits the GCS-8 into its Windows driver layout: 10 stereo inputs and 7 stereo outputs. Each one is
a named PipeWire device, plus a few mixing buses. These live in a pwctl-generated PipeWire config
file, so they exist at every login with fixed node names, and each app remembers its pick.

Each row shows:
- **status:** `ok` (running), `missing` (in the model but not in PipeWire), `off` (turned off in the
  model) or `?` (`pw-dump` unavailable)
- **label:** what apps show
- **channels:** e.g. `AUX8/9`
- **node name:** fixed

Inputs, outputs and buses are grouped. Under the buttons, a note says whether the config on disk
matches `devices.json`, and a two-line key legend is always shown.

| Key | Mouse | Action |
|---|---|---|
| ↑ ↓ | click a row, or wheel | select |
| Enter | double-click a row | rename (label only; the node name never changes) |
| Space | | turn a pair on or off |
| a | [Add bus] | add a bus; asks for a name |
| d | | delete the selected bus, after y/n (pairs can only be turned off) |
| p | [Preview] | show the diff of the config file in `$PAGER` (default `less`) |
| A | [Apply] | write the config file and restart PipeWire, after y/n, then check |
| t | [Test] | play a 1-second quiet noise burst on the selected output pair |

Edits save to `devices.json` straight away. **Nothing reaches PipeWire until `A` is confirmed.**
Apply restarts `pipewire`, `pipewire-pulse` and `wireplumber`, which interrupts audio for a moment.

Default pairs (from the GCS-8 Windows driver, `RDIF1240.INF`):

| Inputs | AUX | Outputs | AUX |
|---|---|---|---|
| GCS-8 MAIN | 0/1 | GCS-8 CH USB | 0/1 |
| GCS-8 MONITOR | 2/3 | GCS-8 MAIN-AUX | 2/3 |
| GCS-8 CH 1 (Guitar) | 4/5 | GCS-8 CH 1-AUX | 4/5 |
| GCS-8 CH 2 (Mic) | 6/7 | GCS-8 CH 2-AUX | 6/7 |
| GCS-8 CH 3 | 8/9 | GCS-8 CH 3-AUX (Music) | 8/9 |
| GCS-8 CH 4 | 10/11 | GCS-8 CH 4-AUX (Voip) | 10/11 |
| GCS-8 CH USB | 12/13 | GCS-8 CH SOUND PAD | 12/13 |
| GCS-8 CH MOBILE (Phone) | 14/15 | | |
| GCS-8 CH BLUETOOTH | 16/17 | | |
| GCS-8 CH SOUND PAD | 18/19 | | |

Default buses: SYSTEM and GAME. The config also sets `default.clock.rate = 48000`, and leaves the
buffer size to presets.

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

pwctl devices [list]                 pairs and buses with live status
pwctl devices preview                diff of the config file against what's on disk
pwctl devices apply                  show the diff, ask, write, restart PipeWire, check
pwctl devices rollback               ask, restore the previous config (or remove it), restart
pwctl devices check                  confirm every expected node is running on the right AUX pair
pwctl devices test PAIR              noise burst on an output pair, e.g. `test ch3aux`
```

Exit codes: 0 ok; 1 a PipeWire command failed, a check found problems or you answered no;
2 bad input.

## Files

| Path | Written by | What |
|---|---|---|
| `~/.config/pwctl/presets.json` | Presets view, `preset save/delete` | presets |
| `~/.config/pwctl/devices.json` | Devices view, `devices apply` | the devices model: the source of truth |
| `~/.config/pipewire/pipewire.conf.d/50-pwctl-devices.conf` | `devices apply` only | the generated PipeWire config file ("drop-in") |
| `…/50-pwctl-devices.conf.bak` | `devices apply` | the previous config file, used by rollback |

The drop-in starts with a "Generated by pwctl" header. pwctl refuses to overwrite or roll back a
file at that path without the header.

`devices.json` shape:

```json
{
  "device": {"out": "alsa_output.usb-BOSS_GCS-8-01.pro-output-0",
             "in": "alsa_input.usb-BOSS_GCS-8-01.pro-input-0"},
  "clock_rate": 48000,
  "pairs": [{"id": "ch2", "dir": "in", "aux": 6, "label": "GCS-8 CH 2 (Mic)", "enabled": true}],
  "buses": [{"name": "SYSTEM", "label": "SYSTEM"}]
}
```

Validation rules:
- `aux` is even and in range: 14 output channels, 20 input.
- No two pairs in one direction share an id, channels or a label.
- Labels have no quotes, backslashes or newlines.
- Bus names are 1–32 letters, digits, `-` or `_`.
- `clock_rate` is one of the Settings rates, or `null` to leave it out.

## How the devices work

Each pair is one `libpipewire-module-loopback`:
- **Output pair:** a stereo `Audio/Sink` that apps play into. Its other side plays to the GCS-8 on
  the pair's two AUX channels.
- **Input pair:** captures two AUX channels from the GCS-8 and presents them as a stereo
  `Audio/Source`.
- **Hardware side:** `stream.dont-remix` and `node.passive` are set, so the channels are passed
  straight through and the loopback doesn't keep the device awake on its own.

Each bus is a `support.null-audio-sink` with `media.class = Audio/Duplex` and `object.linger`, the
same kind of device `start_audio.sh` made with `pactl`, but persistent.

Node names:
- **Pairs:** `pwctl.gcs8.<in|out>.<id>`, with `.hw` on the hardware side. They never depend on the
  label.
- **Buses:** the bus name itself, so apps that remember today's `SYSTEM` keep it.

Before the full set goes live, `docs/hand-check.md` proves the mechanism with one pair in each
direction.

## Environment

| Variable | Effect |
|---|---|
| `PWCTL_PRESETS` | use this presets file instead of the default |
| `PWCTL_DEVICES` | use this devices file instead of the default (the hand check uses it) |
| `XDG_CONFIG_HOME` | base for both config files and the drop-in |
| `PWCTL_FIXTURES` | read from recorded output in that folder and drop all writes (demo and tests) |
| `PAGER` | pager for the Devices preview |

## Code map

| File | Role |
|---|---|
| `pwctl.py` | entry point: CLI dispatch, the subprocess runner, the curses `Tui` |
| `pwcore.py` | pure logic: settings metadata, `pw-top` parsing, presets, the UI state machine and screen rendering |
| `pwdevices.py` | pure logic for Devices: discovery, model, config generation, apply/rollback, checks |

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

### pwdevices.py

- **Discovery:**
  - `read_dump(run)`, `nodes(dump)`
  - `find_hw(dump)`: the GCS-8 pro output and input nodes, with their channel counts
- **Model:**
  - `default_model()`, `validate_model(m, channels)`
  - `devices_path()`, `load_model`, `save_model`
  - `pair_node_name(p)`
- **Edits** (each returns a new, validated model):
  - `dev_items(m)`
  - `set_label(m, item, label)`, `toggle_pair(m, dir, id)`
  - `add_bus(m, name)`, `delete_bus(m, name)`
- **Config file:**
  - `render_pair(p, m)`, `render_bus(b)`, `render_dropin(m)`
- **Apply:**
  - `dropin_path()`, `check_ours(path)`
  - `diff_dropin(path, text)`: unified diff against what's on disk
  - `write_dropin(path, text)`: atomic, keeps `.bak`, returns False when unchanged
  - `rollback_dropin(path)`
  - `RESTART_CMD`
- **Checks:**
  - `check_applied(m, dump)`: problems, or `[]` when everything is running on the right channels
  - `pair_status(m, dump)`: node name → running

### pwctl.py

- `main(argv, runner, out, err, execvp, ask)`: no arguments starts the UI; otherwise runs a command.
  `ask` is the y/n prompt, which tests replace.
- `_devices(...)`: the `devices` subcommands.
- `_problems(m, runner, settle)`: re-checks while PipeWire comes back from a restart.
- `_test_pair(runner, m, id)`, `write_noise(path)`: the test burst.
- `Tui`: the curses loop, plus a background `pw-top` reader. It refreshes every second; on the
  Devices tab it also re-reads `pw-dump` and `devices.json`. It carries out the actions the state
  machine returns.

## Tests

```
python3 -m pytest -q                              # everything; no PipeWire calls
PWCTL_LIVE=1 python3 -m pytest tests/test_live.py # read-only checks against the real PipeWire
```

| File | Covers |
|---|---|
| `test_core.py`, `test_presets.py` | settings parsing, ordering, validation, presets |
| `test_ui.py` | Settings, Top and Presets keys, mouse and rendering |
| `test_cli.py` | v1 commands |
| `test_devices.py` | discovery, model, config generation (golden files in `tests/fixtures/golden/`, plus a `spa-json-dump` parse), apply/rollback, checks, edits |
| `test_devices_cli.py` | `pwctl devices …` with a fake runner; restarts are recorded, never run |
| `test_devices_ui.py` | Devices tab keys, mouse, rendering, scrolling, key legend |
| `test_tui_smoke.py` | the real curses UI in a pty against fixtures |
| `test_live.py` | opt-in; writes back only the values already set |

The plan and history are in `docs/devices-plan.md`; current state is in `HANDOFF.md`.
