"""The Mixer's live listeners for a JACK client (REAPER): its meter is linked by hand, and its MIDI
activity tap is found by our child's pid (pw-mididump's node is always named midi-dump)."""
import pipewrench


def test_link_meter_pairs_two_outputs():
    calls = []
    pipewrench.link_meter("pipewrench-meter-playback-114", (75, 212), run=lambda a: calls.append(a) or 0)
    assert calls == [["pw-link", "75", "pipewrench-meter-playback-114:input_FL"],
                     ["pw-link", "212", "pipewrench-meter-playback-114:input_FR"]]


def test_link_meter_feeds_one_output_to_both_sides():
    calls = []
    pipewrench.link_meter("m", (75,), run=lambda a: calls.append(a) or 0)
    assert [c[2] for c in calls] == ["m:input_FL", "m:input_FR"]


def test_link_meter_retries_until_the_node_exists():
    rcs, calls = iter([1, 1, 0, 0]), []
    pipewrench.link_meter("m", (1, 2), run=lambda a: calls.append(a) or next(rcs), tries=5)
    assert len(calls) == 4 and calls[2][2] == "m:input_FL"


def node(i, name, client):
    return {"id": i, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": name, "client.id": client}}}


def test_tap_input_is_our_childs_port():
    dump = [{"id": 5, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.id": 4242}}},
            {"id": 6, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.id": 999}}},
            node(10, "midi-dump", 5), node(11, "midi-dump", 6),
            {"id": 20, "type": "PipeWire:Interface:Port", "info": {"props": {"node.id": 11}}},
            {"id": 21, "type": "PipeWire:Interface:Port", "info": {"props": {"node.id": 10}}}]
    assert pipewrench.tap_input(dump, 4242) == 21
    assert pipewrench.tap_input(dump, 1) is None
