import json
from pathlib import Path

import pytest

import pwconfig as pc
import pwinventory as pi

FIX = Path(__file__).parent / "fixtures"
CH2_IN = "alsa_input.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_in_GCS8_0_6_7__source"


@pytest.fixture
def inv():
    return pi.inventory(json.loads((FIX / "pwdump_inventory.json").read_text()))


def test_default_volume_mute_commands(inv):
    ch2 = inv.find(CH2_IN)
    assert pi.default_cmd(ch2) == ["wpctl", "set-default", "96"]
    assert pi.volume_cmd(ch2, "+5%") == ["wpctl", "set-volume", "96", "5%+"]
    assert pi.volume_cmd(ch2, "-5%") == ["wpctl", "set-volume", "96", "5%-"]
    assert pi.volume_cmd(ch2, "50%") == ["wpctl", "set-volume", "96", "50%"]
    assert pi.volume_cmd(ch2, "0.4") == ["wpctl", "set-volume", "96", "0.4"]
    assert pi.mute_cmd(ch2) == ["wpctl", "set-mute", "96", "toggle"]


@pytest.mark.parametrize("step", ["loud", "5", "+", "200%", "-0%x", ""])
def test_volume_rejects_bad_steps(inv, step):
    with pytest.raises(ValueError):
        pi.volume_cmd(inv.find(CH2_IN), step)


def test_volume_caps_absolute_at_150_percent(inv):
    pi.volume_cmd(inv.find(CH2_IN), "150%")
    with pytest.raises(ValueError):
        pi.volume_cmd(inv.find(CH2_IN), "151%")


def test_monitor_rows_take_no_commands(inv):
    mon = pi.monitor_rows([inv.find("alsa_output.usb-BOSS_GCS-8-01.HiFi__gcs8_stereo_out_GCS8_0_8_9__sink")])[0]
    for build in (pi.default_cmd, pi.mute_cmd, lambda r: pi.volume_cmd(r, "+5%")):
        with pytest.raises(ValueError, match="monitor"):
            build(mon)


def test_profile_command_by_index_or_name(inv):
    gcs = inv.find_card("GCS-8")
    assert pi.profile_cmd(gcs, "HiFi") == ["wpctl", "set-profile", "95", "1"]
    assert pi.profile_cmd(gcs, "3") == ["wpctl", "set-profile", "95", "3"]
    assert pi.profile_cmd(gcs, "pro-audio") == ["wpctl", "set-profile", "95", "3"]
    with pytest.raises(ValueError, match="HiFi, Direct"):
        pi.profile_cmd(gcs, "surround")


def test_bus_conflicts_flag_live_buses_pwctl_does_not_own(inv):
    c = {**pc.default_config(), "buses": [{"name": "SYSTEM", "label": "SYSTEM"}, {"name": "GAME", "label": "GAME"}]}
    live = {r.name for r in inv.rows()}
    # SYSTEM exists live (made by start_audio.sh), and no pwctl drop-in defines it yet
    assert pc.bus_conflicts(c, live, applied_text="") == ["SYSTEM"]
    # once pwctl's own drop-in defines SYSTEM, the live SYSTEM is ours
    applied = pc.render_dropin({**pc.default_config(), "buses": [{"name": "SYSTEM", "label": "SYSTEM"}]})
    assert pc.bus_conflicts(c, live, applied_text=applied) == []
