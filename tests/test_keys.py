import pwkeys as pk


def test_defaults_are_the_keys_pwrench_ships():
    b = pk.bindings({})
    assert b["devices.show_hidden"] == "h" and b["mixer.meters"] == "v" and b["patch.grid"] == "g"
    assert pk.clashes(b) == {}


def test_every_action_has_a_unique_id_and_a_known_screen():
    ids = [a.id for a in pk.ACTIONS]
    assert len(ids) == len(set(ids))
    assert {a.screen for a in pk.ACTIONS} <= set(pk.SCREENS)


def test_overrides_rebind_and_unbind():
    b = pk.bindings({"devices.show_hidden": "z", "mixer.meters": None})
    assert b["devices.show_hidden"] == "z" and b["mixer.meters"] is None


def test_bad_overrides_are_dropped():
    assert pk.clean({"devices.show_hidden": "zz", "nope": "k", "patch.grid": 3, "mixer.meters": None,
                     "patch.ports": "o"}) == {"mixer.meters": None, "patch.ports": "o"}
    assert pk.clean("junk") == {}


def test_overrides_keep_only_what_differs():
    b = pk.bindings({"devices.show_hidden": "z"})
    b["patch.grid"] = "g"  # back to its default
    assert pk.overrides(b) == {"devices.show_hidden": "z"}


def test_translate_maps_a_bound_key_to_the_default():
    b = pk.bindings({"devices.show_hidden": "z"})
    assert pk.translate("devices", "z", b) == "h"
    assert pk.translate("devices", "h", b) is None   # rebound away: swallowed
    assert pk.translate("mixer", "z", b) == "z"      # other screens untouched
    assert pk.translate("devices", "enter", b) == "enter"
    assert pk.translate("devices", "d", b) == "d"    # not an action: passes through


def test_unbound_key_is_swallowed():
    b = pk.bindings({"mixer.meters": None})
    assert pk.translate("mixer", "v", b) is None


def test_swapped_keys():
    b = pk.bindings({"patch.grid": "m", "patch.monitors": "g"})
    assert pk.translate("patch", "m", b) == "g" and pk.translate("patch", "g", b) == "m"
    assert pk.clashes(b) == {}


def test_same_screen_clash_is_flagged_and_the_first_action_wins():
    b = pk.bindings({"devices.add_bus": "m"})
    c = pk.clashes(b)
    assert set(c) == {"devices.monitors", "devices.add_bus"}
    assert "Devices · add bus" in c["devices.monitors"] and "Devices · monitors" in c["devices.add_bus"]
    assert pk.translate("devices", "m", b) == "m"
    assert pk.translate("devices", "a", b) is None


def test_global_clash_is_flagged_and_the_global_key_wins():
    b = pk.bindings({"mixer.meters": "q", "patch.ports": "3", "devices.test": "*"})
    c = pk.clashes(b)
    assert c["mixer.meters"] == "global: quit"
    assert c["patch.ports"] == "global: tab 3"
    assert c["devices.test"].startswith("global: play/pause")
    assert pk.translate("mixer", "q", b) == "q"


def test_same_key_on_different_screens_is_fine():
    assert pk.clashes(pk.bindings({"mixer.meters": "m"})) == {}


def test_refuse_names_the_reason():
    b = pk.bindings({})
    assert pk.refuse(b, "devices.show_hidden", "q") == "q is global: quit"
    assert pk.refuse(b, "devices.show_hidden", "m") == "m is already Devices · monitors"
    assert pk.refuse(b, "devices.show_hidden", "h") is None   # its own key
    assert pk.refuse(b, "devices.show_hidden", "z") is None
    assert pk.refuse(b, "devices.show_hidden", "enter") == "only single keys can be bound"
    assert pk.refuse(b, "devices.show_hidden", " ") == "only single keys can be bound"


def test_settings_keys_share_a_screen():
    b = pk.bindings({"settings.restart": "s"})
    assert "settings.restart" in pk.clashes(b)
