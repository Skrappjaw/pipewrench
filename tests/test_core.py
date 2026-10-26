from pathlib import Path

import pytest

import pwcore as pc

FIX = Path(__file__).parent / "fixtures"


def settings_text():
    return (FIX / "settings.txt").read_text()


# --- 1. metadata parse -------------------------------------------------------

def test_parse_settings_reads_ints():
    s = pc.parse_settings(settings_text())
    assert s["clock.force-quantum"] == 128
    assert s["clock.force-rate"] == 48000
    assert s["clock.min-quantum"] == 32
    assert s["clock.max-quantum"] == 2048
    assert s["log.level"] == 2


def test_parse_settings_reads_allowed_rates_list():
    s = pc.parse_settings(settings_text())
    assert s["clock.allowed-rates"] == [48000]
    multi = "update: id:0 key:'clock.allowed-rates' value:'[ 44100, 48000 96000 ]' type:''"
    assert pc.parse_settings('Found "settings" metadata 31\n' + multi)["clock.allowed-rates"] == [44100, 48000, 96000]


def test_parse_settings_ignores_garbled_lines():
    text = settings_text() + "\n\nnonsense line\nupdate: id:0 key:'x.str' value:'abc' type:''\n"
    s = pc.parse_settings(text)
    assert s["x.str"] == "abc"
    assert s["clock.force-rate"] == 48000


def test_parse_settings_ignores_other_ids():
    text = 'Found "settings" metadata 31\nupdate: id:5 key:\'clock.force-rate\' value:\'96000\' type:\'\''
    assert "clock.force-rate" not in pc.parse_settings(text)


def test_parse_settings_missing_metadata_raises():
    with pytest.raises(pc.MetadataError):
        pc.parse_settings('Metadata "settings" not found\n')


def test_read_settings_uses_runner():
    calls = []

    def run(argv):
        calls.append(argv)
        return 0, settings_text()

    assert pc.read_settings(run)["clock.force-quantum"] == 128
    assert calls == [["pw-metadata", "-n", "settings", "0"]]


# --- 2. setting model --------------------------------------------------------

def test_settings_table_matches_pipecontrol():
    keys = [s.key for s in pc.SETTINGS]
    assert keys == ["clock.force-rate", "clock.force-quantum", "clock.min-quantum",   # screen order (2026-10-26)
                    "clock.max-quantum", "log.level"]
    rate = pc.setting("clock.force-rate")
    assert rate.choices == [0, 44100, 48000, 88200, 96000, 176400, 192000]
    assert pc.setting("clock.min-quantum").choices == [8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]
    assert pc.setting("clock.force-quantum").choices[0] == 0
    assert pc.setting("log.level").choices == [0, 1, 2, 3, 4, 5]


def test_cycle_steps_and_clamps():
    q = pc.setting("clock.force-quantum")
    assert pc.cycle(q, 128, +1) == 256
    assert pc.cycle(q, 128, -1) == 64
    assert pc.cycle(q, 8192, +1) == 8192
    assert pc.cycle(q, 0, -1) == 0


def test_cycle_custom_value_snaps_in_direction():
    q = pc.setting("clock.force-quantum")
    assert pc.cycle(q, 1000, +1) == 1024
    assert pc.cycle(q, 1000, -1) == 512
    assert pc.cycle(q, 99999, +1) == 8192


def test_display_value():
    assert pc.display_value(pc.setting("clock.force-rate"), 0) == "off"
    assert pc.display_value(pc.setting("clock.force-rate"), 48000) == "48000"
    assert pc.display_value(pc.setting("clock.force-quantum"), 1000) == "custom:1000"
    assert pc.display_value(pc.setting("log.level"), 2) == "Warning"
    assert pc.display_value(pc.setting("clock.min-quantum"), None) == "?"


# --- 3. write command + validation ------------------------------------------

def test_set_cmd_exact_argv():
    assert pc.set_cmd("clock.force-quantum", 256) == \
        ["pw-metadata", "-n", "settings", "0", "clock.force-quantum", "256"]


@pytest.mark.parametrize("key,value", [("clock.bogus", 1), ("clock.force-rate", "48k"),
                                       ("clock.force-rate", True), ("clock.force-rate", -1)])
def test_set_cmd_rejects_bad_input(key, value):
    with pytest.raises(ValueError):
        pc.set_cmd(key, value)


def test_validate_min_greater_than_max_is_error():
    cur = pc.parse_settings(settings_text())
    with pytest.raises(ValueError):
        pc.validate(cur, {"clock.min-quantum": 4096})
    with pytest.raises(ValueError):
        pc.validate(cur, {"clock.max-quantum": 16})


def test_validate_forced_buffer_outside_range_warns():
    cur = pc.parse_settings(settings_text())
    warnings = pc.validate(cur, {"clock.force-quantum": 4096})
    assert any("clamp" in w for w in warnings)
    assert pc.validate(cur, {"clock.force-quantum": 256}) == []
    assert pc.validate(cur, {"clock.force-quantum": 0}) == []


def test_validate_rate_not_allowed_warns():
    cur = pc.parse_settings(settings_text())
    assert any("allowed" in w for w in pc.validate(cur, {"clock.force-rate": 96000}))
    # in allowed rates, device rates not read: only the generic reminder (Skrappjaw, 2026-10-25)
    assert pc.validate(cur, {"clock.force-rate": 48000}) == ["Make sure your device(s) support the rate selected."]
    assert pc.validate(cur, {"clock.force-rate": 48000}, devices=[]) == []


def test_ordered_changes_keeps_min_max_valid():
    cur = {"clock.min-quantum": 32, "clock.max-quantum": 2048}
    # raising min above the old max: max must go first
    keys = [k for k, _ in pc.ordered_changes(cur, {"clock.min-quantum": 4096, "clock.max-quantum": 8192,
                                                   "clock.force-quantum": 4096})]
    assert keys == ["clock.max-quantum", "clock.min-quantum", "clock.force-quantum"]
    # lowering max below the old min: min must go first
    keys = [k for k, _ in pc.ordered_changes(cur, {"clock.max-quantum": 16, "clock.min-quantum": 8})]
    assert keys == ["clock.min-quantum", "clock.max-quantum"]
    # force values always after min/max
    keys = [k for k, _ in pc.ordered_changes(cur, {"clock.force-rate": 0, "clock.min-quantum": 64})]
    assert keys == ["clock.min-quantum", "clock.force-rate"]


# --- 4. pw-top parser --------------------------------------------------------

def parse_fixture():
    p = pc.TopParser()
    blocks = []
    for line in (FIX / "pwtop_batch.txt").read_text().splitlines():
        b = p.feed(line)
        if b is not None:
            blocks.append(b)
    last = p.flush()
    if last:
        blocks.append(last)
    return blocks


def test_top_parser_splits_blocks():
    blocks = parse_fixture()
    assert len(blocks) == 3
    assert all(len(b) > 5 for b in blocks)


def test_top_parser_row_fields():
    rows = {r.name: r for r in parse_fixture()[-1]}
    sysrow = rows["SYSTEM"]
    assert sysrow.state == "R"
    assert sysrow.follower is True
    assert sysrow.format == "F32P 2 48000"
    dummy = rows["Dummy-Driver"]
    assert dummy.state in ("S", "C")
    assert dummy.wait == "---"
    assert dummy.follower is False
    assert dummy.format == ""


def test_top_parser_name_with_spaces_and_err():
    p = pc.TopParser()
    hdr = (FIX / "pwtop_batch.txt").read_text().splitlines()[0]
    p.feed(hdr)
    p.feed("R  155    227  44100  24.6us  32.9us  0.01  0.01    2    S16LE 2 44100  + ALSA plug-in [sunvox]")
    (row,) = p.flush()
    assert row.name == "ALSA plug-in [sunvox]"
    assert row.err == 2
    assert row.id == 155 and row.quant == 227 and row.rate == 44100
    assert row.follower is True


def test_top_parser_without_header_ignores_lines():
    p = pc.TopParser()
    assert p.feed("R   39      0      0   0.0us") is None
    assert p.flush() == []
