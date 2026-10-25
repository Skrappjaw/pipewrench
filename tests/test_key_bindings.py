import json
from pathlib import Path

import pwconfig as pcfg
import pwcore as pc
import pwinventory as pi

FIX = Path(__file__).parent / "fixtures"
CUR = pc.parse_settings((FIX / "settings.txt").read_text())
INV = pi.inventory(json.loads((FIX / "pwdump_inventory.json").read_text()))


def dv():
    return pc.devices_view(INV, pcfg.default_config())


def key(st, k, devices=None):
    return pc.handle_key(st, k, CUR, [], devices=devices or dv())


def chips(st):
    return [k for k, _ in pc.legend(st)]


def test_a_rebound_key_runs_the_action_and_the_old_one_does_nothing():
    st = pc.UIState(tab="devices", keys={"devices.show_hidden": "z"})
    assert key(st, "z")[0].dev_show_hidden
    assert not key(st, "h")[0].dev_show_hidden


def test_an_unbound_action_has_no_key_and_no_chip():
    st = pc.UIState(tab="mixer", keys={"mixer.meters": None})
    assert "v" not in chips(st)
    st2, _ = pc.handle_key(st, "v", CUR, [], mixer=None)
    assert st2.meters


def test_legend_shows_the_bound_key():
    st = pc.UIState(tab="devices", keys={"devices.show_hidden": "z"})
    assert "z" in chips(st) and "h" not in chips(st)
    st = pc.UIState(tab="patch", patch_ports=True, keys={"patch.ports": "o"})
    assert "o Esc" in chips(st)


def test_clashing_binding_drops_out_of_the_legend():
    st = pc.UIState(tab="devices", keys={"devices.add_bus": "m"})
    assert chips(st).count("m") == 1


def test_global_keys_still_win():
    st = pc.UIState(tab="devices", keys={"devices.show_hidden": "q"})
    assert key(st, "q")[1] == [("quit",)]


def test_settings_keys_rebind_too():
    st = pc.UIState(tab="settings", keys={"settings.save": "w"})
    assert pc.handle_key(st, "w", CUR, [])[1] == [("prompt_save",)]
    assert pc.handle_key(st, "s", CUR, [])[1] == []


def test_ui_json_keeps_overrides(tmp_path):
    p = tmp_path / "ui.json"
    st = pc.UIState(keys={"devices.show_hidden": "z", "mixer.meters": None})
    pc.save_ui(p, pc.ui_prefs(st))
    assert pc.load_ui(p)["keys"] == {"devices.show_hidden": "z", "mixer.meters": None}
    p.write_text('{"keys": {"devices.show_hidden": "zz", "bogus": "k"}}')
    assert pc.load_ui(p)["keys"] == {}
