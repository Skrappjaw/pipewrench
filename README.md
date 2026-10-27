# PipeWrench

A terminal control panel for PipeWire on Linux. Volume and mute, default devices, sound card profiles,
virtual buses, a patch bay, network audio (VBAN), sample rate and buffer size, and a realtime check, all live. It never restarts
PipeWire unless you ask.

![The Mixer](docs/guide/mixer.png)

**How to use it: [the user guide](docs/guide.md).**

## Install

Needs Python 3 and PipeWire's tools (`pw-dump`, `pw-metadata`, `pw-top`, `pw-link`, `wpctl`). No other packages.

```
git clone https://github.com/Skrappjaw/pipewrench.git
cd pipewrench
ln -s "$PWD/pipewrench.py" ~/.local/bin/pwrench
```

## Run

```
pwrench
```

Six tabs: **Mixer** (levels and mute), **Devices** (cards, buses and MIDI devices), **Patch** (what goes where, audio and MIDI),
**Settings** (audio clock and realtime; network streams; display and keys), **Top** (`pw-top`) and **About**. `Tab` or `1`–`6` switches, `q`
quits, and every tab lists its keys at the bottom. The mouse works too.

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
pwrench vban [list] | detect [SECONDS] | add in|out NAME IP [--to|--from NODE] [--midi]
pwrench vban on|off|remove in|out NAME
pwrench theme list | export ID [FILE] | check [FILE]
```

What each one does: [the user guide's Command line section](docs/guide.md#command-line).

Make your own colours: [the theme guide](docs/themes.md).

## Where it keeps things

- Your settings: `~/.config/pipewrench/`
- Your themes: `~/.config/pipewrench/themes/*.toml`
- Buses and the clock: `~/.config/pipewire/pipewire.conf.d/50-pipewrench-devices.conf`
- Device names: `~/.config/wireplumber/…/51-pipewrench-labels.*`
- Network streams (VBAN): `~/.config/pipewrench/vban/` and `~/.config/systemd/user/pipewrench-vban@.service`

PipeWrench only touches files it made, and keeps the previous version so `pwrench devices rollback` can undo.
