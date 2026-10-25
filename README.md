# PipeWrench

A terminal control panel for PipeWire on Linux. Volume and mute, default devices, sound card profiles,
virtual buses, a patch bay, sample rate and buffer size, and a realtime check, all live. It never restarts
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

Six tabs: **Mixer** (levels and mute), **Devices** (cards and buses), **Patch** (what goes where),
**Settings** (clock, display, keys, presets), **Top** (`pw-top`) and **About**. `Tab` or `1`–`6` switches, `q`
quits, and every tab lists its keys at the bottom. The mouse works too.

## Command line

```
pwrench status | rate N | buf N | min N | max N | reset
pwrench preset list | save NAME [LABEL] | apply NAME | delete NAME
pwrench devices [list] | default DEV | volume DEV STEP | mute DEV | profile CARD PROFILE | test DEV
pwrench devices label DEV TEXT | hide NAME | bus add NAME | bus delete NAME
pwrench devices preview | apply [--restart] | rollback | restart
pwrench patch [list] | connect SRC DST | disconnect SRC DST
pwrench realtime
```

## Where it keeps things

- Your settings: `~/.config/pipewrench/`
- Buses and the clock: `~/.config/pipewire/pipewire.conf.d/50-pipewrench-devices.conf`
- Device names: `~/.config/wireplumber/…/51-pipewrench-labels.*`

PipeWrench only touches files it made, and keeps the previous version so `pwrench devices rollback` can undo.
