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
    return pc.handle_key(st, k, CUR, devices=devices or dv())


def chips(st):
    return [k for k, _ in pc.legend(st)]


def test_a_rebound_key_runs_the_action_and_the_old_one_does_nothing():
    st = pc.UIState(tab="devices", keys={"devices.show_hidden": "z"})
    assert key(st, "z")[0].dev_show_hidden
    assert not key(st, "h")[0].dev_show_hidden


def test_an_unbound_action_has_no_key_and_no_chip():
    st = pc.UIState(tab="mixer", keys={"mixer.meters": None})
    assert "v" not in chips(st)
    st2, _ = pc.handle_key(st, "v", CUR, mixer=None)
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
    assert key(st, "q")[0].popup == ("quit",)  # q still asks to quit; the binding never fires


def test_ui_json_keeps_overrides(tmp_path):
    p = tmp_path / "ui.json"
    st = pc.UIState(keys={"devices.show_hidden": "z", "mixer.meters": None})
    pc.save_ui(p, pc.ui_prefs(st))
    assert pc.load_ui(p)["keys"] == {"devices.show_hidden": "z", "mixer.meters": None}
    p.write_text('{"keys": {"devices.show_hidden": "zz", "bogus": "k"}}')
    assert pc.load_ui(p)["keys"] == {}


# --- Settings → Keys ---------------------------------------------------------------------

def prefs(**kw):
    """Settings → Preferences with the keys in its pane."""
    return pc.UIState(tab="settings", settings_cat="prefs", settings_focus="pane", **kw)


def pane(t):
    return t[pc.MENU_W:]


def row_of(action_id):
    return pc.KEYS_ROW + next(n for n, (k, a) in enumerate(pc.BINDING_ROWS) if k == "action" and a.id == action_id)


def binding_line(lines, label):
    """The Key Bindings row for an action label, not a Display row that starts the same way."""
    return next((t, s) for t, s in lines if pane(t).strip().lstrip("> ").startswith(label) and "◀" not in t)


def skey(st, *keys):
    actions = []
    for k in keys:
        st, actions = pc.handle_key(st, k, CUR)
    return st, actions


def text(st, height=80):
    lines, _ = pc.render_screen(st, CUR, "", 100, height)
    return lines


def test_keys_section_follows_display_in_preferences():
    body = "\n".join(t for t, _ in text(prefs()))
    assert body.index("Display") < body.index("Key Bindings") < body.index("Reset all keys")
    kb = body.index("Key Bindings")
    assert body.index("│  Devices\n", kb) < body.index("Show hidden ", kb)   # under its view's heading
    assert pc.RESET_KEYS_ROW == pc.KEYS_ROW + len(pc.BINDING_ROWS)


def test_enter_then_a_key_rebinds_and_saves():
    st = prefs(cursor=row_of("devices.show_hidden"))
    st, actions = skey(st, "enter")
    assert st.key_capture == "devices.show_hidden" and "press a key" in st.message and actions == []
    st, actions = skey(st, "z")
    assert st.keys == {"devices.show_hidden": "z"} and st.key_capture is None and actions == [("save_ui",)]
    line = binding_line(text(st), "Show hidden")[0]
    assert " z " in line and "*" in line


def test_capture_refuses_global_and_same_screen_keys():
    st = prefs(cursor=row_of("devices.show_hidden"))
    for k, why in (("q", "global: quit"), ("m", "already Devices · monitors"), ("3", "global: tab 3")):
        st2, actions = skey(st, "enter", k)
        assert actions == [] and why in st2.message and st2.failed == st2.message
        assert st2.keys == {} and st2.key_capture is None and st2.tab == "settings"


def test_capture_esc_cancels_and_backspace_unbinds():
    st = prefs(cursor=row_of("mixer.meters"))
    st2, actions = skey(st, "enter", "esc")
    assert st2.keys == {} and actions == [] and st2.key_capture is None
    st2, actions = skey(st, "enter", "backspace")
    assert st2.keys == {"mixer.meters": None} and actions == [("save_ui",)]
    st3, actions = skey(st, "delete")  # straight from the row too
    assert st3.keys == {"mixer.meters": None} and actions == [("save_ui",)]
    line = binding_line(text(st2), "Meters")[0].split("Meters", 1)[1].split()[0]
    assert "—" in line


def test_rebinding_back_to_the_default_clears_the_override():
    st = prefs(cursor=row_of("mixer.meters"), keys={"mixer.meters": "b"})
    st, _ = skey(st, "enter", "v")
    assert pc.ui_prefs(st)["keys"] == {}


def test_reset_all_asks_first():
    st = prefs(cursor=pc.RESET_KEYS_ROW, keys={"mixer.meters": "b"})
    st, actions = skey(st, "enter")
    assert st.popup == ("reset_keys",) and actions == []
    assert "Reset every key" in "\n".join(t for t, _ in text(st))
    st, actions = skey(st, "left", "enter")
    assert st.keys == {} and actions == [("save_ui",)]


def test_hand_edited_clash_is_flagged_red():
    st = prefs(keys={"devices.add_bus": "m", "mixer.meters": "q"})
    lines = text(st)
    row = binding_line(lines, "Add bus")
    assert "also Devices · monitors" in row[0] and (pc.MENU_W, len(row[0]), "alert") in row[1][1]
    assert "global: quit" in binding_line(lines, "Meters")[0]


def test_keys_rows_have_their_own_legend():
    st = prefs(cursor=row_of("mixer.meters"))
    assert [k for k, _ in pc.legend(st)][:2] == ["Bksp", "Esc"]
    st, _ = skey(st, "enter")
    assert [k for k, _ in pc.legend(st)] == ["Esc", "Bksp"]


def test_left_right_do_nothing_on_key_rows():
    st = prefs(cursor=row_of("mixer.meters"))
    assert skey(st, "right")[1] == [] and skey(st, "left")[0].keys == {}
