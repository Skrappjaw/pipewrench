# PipeWrench

**A terminal control panel for PipeWire, the sound system on modern Linux.** Levels, devices, routing, the
audio clock and network audio, on one screen you can drive with the keyboard, the mouse or a touchscreen.
It runs the same in a desktop terminal, over SSH, in a tmux pane next to your DAW, or on a machine with no
desktop at all.

![PipeWrench's Mixer: every output, input and app with its level and meter](docs/guide/mixer.png)

[Why it exists](#why-it-exists) · [What it does](#what-it-does) · [Screens](#screens) ·
[Where it fits](#where-it-fits) · [Install](#install) · [Run](#run) · [Command line](#command-line) ·
[Where it keeps things](#where-it-keeps-things) · [Licence](#licence) · **[User guide](docs/guide.md)**

## Why it exists

> **Draft for Skrappjaw to rewrite in his own words. Remove this line when done.**

I record on Linux through a BOSS GCS-8, which PipeWire shows as separate stereo channels, ten in and seven
out. Getting a session ready meant running a script that restarted PipeWire and forced the sample rate,
and the restart dropped Discord every time. The graphical tools each handled one piece: pavucontrol for
levels, qpwgraph for patching, nothing for the clock. None of them would run over SSH or sit in a tmux
pane beside REAPER. I wanted one tool that did all of it from the terminal and never restarted anything
without asking, so I wrote it.

— Skrappjaw

## What it does

- **Mixer.** Volume, mute and live meters for every output, input and app. Make any channel the system
  default from its menu. Going past 100% asks first.
- **Devices.** Every sound card with its channels and profiles. Rename channels, hide the ones you never
  use, and add virtual buses (one for OBS, one for Discord) that survive a reboot.
- **Patch.** A patch bay for audio and MIDI. Connect any output to any input, as a whole device or port by
  port. REAPER and other JACK apps show up alongside everything else.
- **Audio settings.** Sample rate and buffer size, changed live, with the latency in milliseconds, the
  rates each card supports, and a realtime check that shows the exact fix when it's off.
- **Network audio.** Send and receive audio and MIDI between machines over VBAN, the protocol Voicemeeter
  uses. Streams you turn on come back at login.
- **Command line.** Everything above is also a `pwrench` command, for scripts and remote machines.
- **Themes.** Eleven built in, and your own as small text files.

PipeWrench never restarts PipeWire or WirePlumber unless you ask, because a restart drops Discord, OBS and
anything else connected at the time. When a change needs one, it tells you and lets you pick the moment.

## Screens

<table>
<tr>
<td width="50%"><img src="docs/guide/patch.png" alt="The Patch tab"><br><b>Patch</b>: what plays into what, audio and MIDI.</td>
<td width="50%"><img src="docs/guide/settings.png" alt="Settings, Audio"><br><b>Settings → Audio</b>: rate, buffer, realtime.</td>
</tr>
<tr>
<td width="50%"><img src="docs/guide/network.png" alt="Settings, Network"><br><b>Settings → Network</b>: VBAN streams in and out.</td>
<td width="50%"><img src="docs/guide/devices.png" alt="The Devices tab"><br><b>Devices</b>: cards, profiles and buses.</td>
</tr>
</table>

## Where it fits

- **It's a terminal app.** There's no desktop window. The mouse and touch work inside the terminal.
- **There are no effects.** PipeWrench sets levels and routes audio. For EQ, compression and the like, use
  EasyEffects or a plugin host.
- **Tested on Linux Mint 22** (PipeWire 1.0.5, WirePlumber 0.4) with a BOSS GCS-8, a Behringer UMC404HD
  and onboard audio. WirePlumber 0.5 is supported in the code but hasn't been run on real hardware yet.
  Other distributions should work and haven't been tested.
- **Network audio has limits on PipeWire before 1.4.2**, which Mint 22 ships. The guide's
  [Network](docs/guide.md#network) section explains them.

## Install

You need:

- Python 3 (tested on 3.12). PipeWrench uses only the standard library.
- PipeWire's command-line tools: `pw-dump`, `pw-metadata`, `pw-top`, `pw-link`, `wpctl`. Most
  distributions install them with PipeWire.
- Optional: `nmap`, which helps Settings → Network find other computers. On PipeWire before 1.4.2, Detect
  depends on it ([why](docs/guide.md#network)).

```
git clone https://github.com/Skrappjaw/pipewrench.git
cd pipewrench
ln -s "$PWD/pipewrench.py" ~/.local/bin/pwrench
```

## Run

```
pwrench
```

Six tabs: **Mixer**, **Devices**, **Patch**, **Settings** (audio, network, preferences), **Top** (`pw-top`)
and **About**. `Tab` or `1`–`6` switches tabs, `Enter` opens a row's menu, and `q` quits. Each tab lists its
shortcut keys at the bottom. The [user guide](docs/guide.md) walks through every screen.

## Command line

```
pwrench status | realtime | top
pwrench rate N | buf N | min N | max N | log N | reset
pwrench devices [list] [--hidden] [--monitors] | default DEV | volume DEV STEP | mute DEV | test DEV
pwrench devices profile CARD PROFILE | restore
pwrench devices label DEV TEXT | label DEV --clear | hide NAME | unhide NAME
pwrench devices bus add NAME [LABEL] | bus delete NAME
pwrench devices preview | apply [--restart] | rollback | restart
pwrench patch [list] | connect SRC DST | disconnect SRC DST
pwrench vban [list] | detect [SECONDS] | scan | add in|out NAME IP [--to|--from NODE] [--midi]
pwrench vban connect in|out NAME NODE | disconnect in|out NAME | on|off|remove in|out NAME
pwrench theme list | export ID [FILE] | check [FILE]
```

Every option is explained in the guide's [Command line](docs/guide.md#command-line) section. To make your
own colours, see the [theme guide](docs/themes.md).

## Where it keeps things

- Your settings: `~/.config/pipewrench/`
- Your themes: `~/.config/pipewrench/themes/*.toml`
- Buses and the clock: `~/.config/pipewire/pipewire.conf.d/50-pipewrench-devices.conf`
- Device names: `~/.config/wireplumber/…/51-pipewrench-labels.*`
- Network streams (VBAN): `~/.config/pipewrench/vban/` and `~/.config/systemd/user/pipewrench-vban@.service`

PipeWrench only touches files it created, and keeps the previous version so `pwrench devices rollback` can
undo a change.

## Licence

Copyright © 2026 Skrappjaw. PipeWrench is free software under the GNU General Public License, version 3
or (at your option) any later version. See [LICENSE](LICENSE).
