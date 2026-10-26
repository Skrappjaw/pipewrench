# PipeWrench user guide

PipeWrench is a terminal control panel for PipeWire: volume and mute, the default devices, sound card
profiles, virtual buses, a patch bay, the clock and buffer settings, and a realtime check. It changes
PipeWire live and never restarts anything unless you ask.

Start it with `pwrench`. The command line and where files live are in [`README.md`](../README.md); this
guide is about using the screen.

> The pictures are drawn from the test data by `tools/screenshots.py`, so device names are from the
> author's BOSS GCS-8 setup. Yours will show your own devices.

## The screen

![The Mixer](guide/mixer.png)

- **Tabs** along the top: Mixer, Devices, Patch, Settings, Top, About. Switch with `Tab` (`Shift+Tab`
  goes back), the number keys `1`–`6`, or a click. Leaving a tab resets it: open menus close and
  unapplied Settings changes are dropped, so a tab always opens clean.
- **The keys box** at the bottom lists every key the current view takes. Each key is a chip, and
  clicking a single-key chip presses it.
- **The status band** under it shows what just happened: `✓` on green when it worked, `✖` on red when it
  didn't. It stays until the next message. A y/n question shows there too; any key other than `y` cancels it.
- **The now-playing bar** above the keys shows the media player that's playing: `*` play/pause,
  `-` `+` its volume, `<` `>` previous and next. There's no stop on purpose (Brave can't restart a
  stopped tab from outside).
- **The mouse wheel scrolls** whatever list you're in, wherever the pointer is. It never changes a
  value, so scrolling a long list is safe.
- `q` quits from anywhere.

Menus open with Enter and close with Esc (or any other key). Anything that moves audio for every app,
like changing the default device, goes through a menu so a stray key can't do it.

## Mixer: everyday levels

Every output and input, then the apps playing or recording under each. ★ marks the system default.

| Key | Does |
|---|---|
| ↑ ↓ | select a channel |
| ← → | volume −5% / +5%. → stops at 100%; press it again there to unlock that channel up to 150% |
| mouse wheel | scroll the list (it never changes volume) |
| Space | mute or unmute |
| Enter (double-click) | the channel's menu |
| v | live meters on or off |

The volume % sits inside the bar. Green is up to 100%, red past it, grey when muted. Past 100% can clip, so
at 100% the first → only warns; the second unlocks that channel. It locks again once it's back under 100%.
The media bar's `+` stops at 100%. On the command line, `pwrench devices volume DEV +5%` goes up to 150%. The meter to the
right shows the live level of running channels (yellow past −18 dB, red past −6 dB) with a short peak
hold.

An app with MIDI (REAPER) gets a second row for it, `REAPER · MIDI`, under its audio row: where its
MIDI goes (`→ KB16-01 MIDI 1, nanoKONTROL2 _ CTRL`) or comes from (`←`). Where the volume would be, a
dot lights (● MIDI) as MIDI passes and goes dim (○ MIDI) when it stops. The dot works while meters
are on (`v`).

**Prefer numbers?** Settings → Display → Volume bar off leaves just the %, and the meter moves in
beside it:

![Mixer with the volume bar off](guide/mixer-bar-off.png)

### The channel menu

![A Mixer channel's menu](guide/mixer-menu.png)

- **Set as system default:** every app that follows the default moves to this channel. It's first in
  the menu, so Enter, Enter does it.
- **Mute** / **Test sound** (outputs play a short noise burst).
- **Rename…** gives the channel your own name, say "Mic" for GCS-8 CH 2. An empty name puts the
  original back.
- **Card profile…** jumps to the channel's sound card on Devices with its profiles open.

Renaming asks one more question:

![Renaming a channel](guide/mixer-rename.png)

WirePlumber reads names when it starts, so a new name shows after a WirePlumber restart.
PipeWrench asks "Restart WirePlumber now to show it?" Say `n` if you're on a call: a restart drops
apps like Discord, and some don't reconnect on their own. The name is saved either way and appears at
the next restart or login.

## Devices: hardware and buses

The sound cards with their profiles, and the virtual buses. A card's channels are listed under it,
dimmed: they're there so you can see what a profile gives you. Channel work (default, rename, test,
levels) is on the Mixer.

![Devices](guide/devices.png)

| Key | Does |
|---|---|
| Enter | the card's or bus's menu |
| p | the card's profile menu |
| h | show hidden cards and buses (dimmed) |
| a | add a bus |
| x | delete a bus (after y/n) |
| t | test sound on a bus |
| m | list each output's monitor |
| A | write the generated files (names, buses, clock) |
| R | restart WirePlumber (after y/n) |

Cards on profile `off` (unused HDMI, a webcam you don't use for sound) wait at the bottom under
**Off**.

MIDI devices (a controller, the GCS-8's MIDI ports, a macro pad) are listed under **MIDI**, after the
cards. They're read-only: each shows its ALSA card (`hw:3`) and what it's connected to
(`← REAPER  → REAPER`). Connect them on Patch.

### Switch a card's profile

![A card's profile menu](guide/devices-profile.png)

● marks the active profile. Picking another asks y/n first, because audio on that card drops for a
moment. For the GCS-8, **HiFi** is the split profile (one channel pair per device). Picking `off`
turns the card off completely.

### Hide a device you never use

Select its card and press Enter → Hide card (there's no shortcut, so a stray key can't hide a card). Hidden cards leave the Mixer too. `h` shows
them again, dimmed, so you can unhide one. Most people want every channel; hiding is for whole
devices, like onboard audio you never plug into.

### Virtual buses

A bus is a virtual device apps can play into or record from, for example a "Stream" bus that OBS
records and your music plays into. Press `a` (or Enter on **+ Add bus…**) and give it a name:

![Adding a bus](guide/devices-add-bus.png)

It's created at once, with no restart. A bus's own menu hides or deletes it:

![A bus's menu](guide/devices-bus.png)

Name a bus on the Mixer like any channel; a bus shows its new name after the next login.

## Patch: what goes where

Sources on the left (apps playing, inputs, buses), destinations on the right (apps recording, outputs,
buses). Select a source, then `→` into the destinations and press Enter to connect or disconnect.

![Patch, column view](guide/patch.png)

- ● next to a destination: the selected source goes there. `●3` next to a source: it feeds three
  destinations.
- The line under the lists names everything the selected source feeds.
- Connecting pairs channels up (FL→FL, FR→FR). A mono source feeds both.
- Links made here are live. PipeWire may not keep them after a restart; saved patch sets are planned
  with scenes.

| Key | Does |
|---|---|
| ↑ ↓ | select |
| ← → | switch between sources and destinations |
| Enter | connect or disconnect |
| g | grid view |
| p | port matrix for the selected pair |
| m | show every monitor |

### Monitors (OBS desktop audio)

A **monitor** is a copy of what an output is playing. OBS's Desktop Audio records an output's monitor,
not the output itself. Monitors that something records from always show, under their own heading;
`m` adds the rest.

![A monitor feeding OBS](guide/patch-monitors.png)

So if OBS is set to GCS-8 CH USB but the CH USB input looks unconnected, look under **Monitors**:
`GCS-8 CH USB (monitor) ●1` is the one OBS takes.

App streams carry their own names when they give one: OBS's inputs read `OBS · Desktop Audio`,
`OBS · Mic/Aux`.

### MIDI

MIDI ports are listed after the audio, under a **MIDI** heading on both sides: controllers and
interfaces by their port name (`nanoKONTROL2 _ CTRL`, `GCS-8 MIDI 1`), and apps like REAPER by name.
Connect them the same way, with Enter. MIDI only connects to MIDI; Enter on an audio destination says
so and changes nothing.

From the command line, a MIDI source picks the app's MIDI input:
`pwrench patch connect "nanoKONTROL2 _ CTRL" REAPER`.

### Grid view

`g` shows every source against every destination: ● connected, ◐ one channel only, · not connected.
The numbers across the top are the destinations in the key below the grid.

![Patch, grid view](guide/patch-grid.png)

### Recording into REAPER's inputs

REAPER (through `pw-jack`) shows its inputs as plain in1, in2… Enter on REAPER as a destination
opens a slot menu, starting at the next free pair:

![Choosing REAPER's inputs](guide/patch-slots.png)

Pick **In 3-4** and the selected source records into REAPER's inputs 3 and 4.

### Port by port

`p` opens a port matrix for the selected pair, for patches like CH 2's left channel into REAPER in3
only. Enter links or unlinks the port under the cursor.

![The port matrix](guide/patch-ports.png)

## Settings

A menu on the left picks the category, and the category opens on the right. Settings opens on the
menu: ↑↓ picks **Audio** or **Preferences**, and Enter or → goes into it. ← or Esc comes back to the
menu, and coming back drops any unapplied change. A click on a category opens it.

A value never changes on a stray arrow: **Enter opens a row**, then ←→ change it. Enter applies (or keeps)
it, Esc puts the row back, and ↑↓ close it, keeping the change. A tap on a row's ◀ ▶ opens it and steps it
in one go.

![Settings, with a realtime problem](guide/settings.png)

### Audio

#### Clock

PipeWire's sample rate and buffer sizes. An open row's change is marked `*` until applied. Smaller
buffers mean less latency and more risk of dropouts. Each buffer row shows its latency at the rate in
use, and the rate row the latency of the buffer in use: one buffer's period, buffer ÷ rate (128 at 48k
is 2.67 ms; 128 at 192k is 0.67 ms). It's not the round trip, which adds the interface's own buffers. **Force buffer size** is the one to change for
tracking (128 or lower) versus mixing (1024).

Below the rows, each sound card that's on lists the rates it supports and the rate it's running at,
for example `UMC404HD 192k  44.1 48 88.2 96 176.4 192k · running 48k`. The list comes from the
kernel, and only USB interfaces report one; other cards say `not reported`. When you change the rate:

- a card that can't run it is named (`GCS-8 can't run at 192k (it supports 48k)`);
- a rate outside **allowed rates** runs the graph at that rate while your devices stay at the default
  rate and PipeWire resamples, and the warning says so;
- if any card doesn't report its rates: "Make sure your device(s) support the rate selected."

Under the clock rows are three buttons. ↓ to their row, ←→ picks one, and Enter or a click presses it:

- **Apply** sets the changes live, now. Enter on an open clock row does the same, and Esc reverts.
- **Save** keeps the live clock for the next time PipeWire starts (`s` saves from anywhere in
  Settings). It writes to PipeWrench's PipeWire file, `~/.config/pipewire/pipewire.conf.d/50-pipewrench-devices.conf`,
  and never restarts anything. A forced rate or buffer is saved as PipeWire's default, so apps can still
  ask for another size after a restart. Values that aren't forced, or match PipeWire's own defaults,
  aren't written. Apply any changes before saving.
- **Reset** loads PipeWire's defaults (no forced rate or buffer, min 32, max 2048) as changes marked
  `*`. Apply sets them live, and Save after that clears the saved clock.

#### Realtime

Whether PipeWire's audio thread runs with realtime priority. Without it, anything else on the computer
can interrupt the audio, and you hear stutters and dropouts, for example on a Discord call.

- **pipewire group:** realtime is granted to members of the `pipewire` group.
- **realtime limit:** what PipeWire actually got. 0 means it started without the permission.
- **audio threads:** each of PipeWire's audio threads, realtime or not.
- **portal refusals:** for information. Apps ask the desktop portal for realtime too, and it refuses
  them; that's harmless once PipeWire itself is realtime.
- **fix:** the command to run in a terminal. PipeWrench never runs `sudo` itself.

The usual fix is `sudo usermod -aG pipewire $USER` and then a **reboot**. Logging out isn't always
enough, because the session that starts PipeWire can stay running. When it's right, every row is
green:

![Settings, realtime working](guide/settings-realtime-ok.png)

`pwrench realtime` prints the same check in a terminal and exits 0 when all is well.

### Preferences

PipeWrench's own settings. Open a row with Enter; ←→ then change it live (a theme shows at once), Enter
keeps it and Esc puts it back. They're kept in `~/.config/pipewrench/ui.json`.

![Settings → Preferences](guide/settings-preferences.png)

#### Display

Theme (dark, light, crowbar orange, Pipe Boy in green, amber, cyan or white, Vaporware, Pipecraft, or
pipeOS-lite and pipeOS-dark), the meters' frame rate, fall speed and peak hold, and the
Mixer's volume bar.

**Pipe Boy** is one phosphor colour, after Fallout's Pip-Boy: green, amber, cyan or white. With no other
colours, status shows by style: good is bold, waiting is underlined, and a problem is a pale block.

![Pipe Boy](guide/mixer-pipeboy.png)

**Vaporware** is pink, cyan and mint on dark purple.

![Vaporware](guide/mixer-vaporware.png)

**Pipecraft** uses Minecraft's text colours on dark dirt, and its About tab says PIPECRAFT.

![Pipecraft](guide/about-pipecraft.png)

**pipeOS-lite** (a clean test chamber) and **pipeOS-dark** (Portal 2's moody chambers) follow one rule:
anything with a direction is a portal colour, blue for playback, outputs and sources and orange for
recording, inputs and destinations. Everything else uses blue.

- **Patch:** blue dots and cursor bar on the sources, orange on the destinations. The slot menu is orange.
- **Mixer:** Playback's heading, cursor bar and menu are blue; Recording's are orange. Volume bars are
  panel grey, and the level meters run blue, orange, then red.
- **Devices:** each channel's `out` is blue and `in` orange.
- **Everything else:** the cursor bar, tabs, titles, key chips and the Settings menu are blue.
- **Status dots stay plain.** Realtime's dots, and the ● and ○ marking a card's profile, show their status in
  white, yellow or red, never a portal colour. There's no green anywhere.

![pipeOS-dark](guide/patch-portal.png)

Every theme except dark and light needs a 256-colour terminal; on a basic one PipeWrench shows dark instead.

#### Keys

Every letter shortcut, one row each, with the key it's on. Enter on a row, then press the new key.
Backspace (or Delete) takes the key off, so the shortcut does nothing; Esc cancels. **Reset all keys**
puts every shortcut back after a y/n.

- A key that's taken on the same screen, or one that works everywhere (`q`, `1`–`6`, the media keys
  `* - + < >`, `y` and `n`), is refused and the reason shows on the status line.
- A clash that's already in the file, from hand editing, shows red with the reason. The first shortcut
  on that screen keeps the key.
- Arrows, Enter, Esc, Space and Tab can't be rebound.
- The key legend at the bottom of each tab shows your keys.

![Settings → Keys](guide/settings-keys.png)

## Top

A live `pw-top`: every node's quantum, rate, timing and errors (ERR). Rows with errors show red. Use
it to see what's running and whether anything is dropping out. ↑↓ and PgUp/PgDn scroll.

![Top](guide/top.png)

## About

The versions of PipeWire and WirePlumber, and where PipeWrench keeps its settings. The logo changes with
the theme: dark and light show the PipeWrench wrench, and Crowbar, the Pipe Boys and Vaporware each
have their own lettering.

![About](guide/about.png)

## When something's wrong

| Symptom | Look at |
|---|---|
| Stutter, dropouts, "robot voice" on calls | Settings → Realtime; Top's ERR column |
| Friends can't hear you | Mixer: is the right input ★ default, unmuted, with a moving meter? |
| OBS isn't getting desktop audio | Patch → Monitors: is the output's monitor connected to OBS? |
| A device is missing from the Mixer | Devices: is its card hidden (H) or on profile `off`? |
| A new name doesn't show | It shows after a WirePlumber restart (Devices → R) or the next login |
| A red "PipeWire not answering" line under the tabs | PipeWire is up but hung. The screen keeps the last thing it saw and the keys still work; PipeWrench checks every 5 s and says "PipeWire is back" when it answers. It never restarts anything itself: `systemctl --user status pipewire wireplumber`, then restart them if you choose |

## Redrawing the pictures

```
python3 tools/screenshots.py
```

It draws every picture in `docs/guide/` from the test fixtures, so they stay in step with the UI. It
needs Pillow (`python3-pil`); PipeWrench itself doesn't.
