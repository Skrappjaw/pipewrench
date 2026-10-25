"""A PipeWire tool that hangs (pw-dump while WirePlumber restarts, Skrappjaw 2026-10-24) is a failed command,
not a crash: the runner returns exit 124 and the views show a message, then recover on the next refresh."""
import pytest

import pipewrench


def test_a_hung_command_is_a_failed_command(monkeypatch):
    monkeypatch.setattr(pipewrench, "RUN_TIMEOUT", 0.2)
    rc, out = pipewrench.default_runner(["sleep", "5"])
    assert rc == 124 and "timed out" in out and "sleep" in out


def test_a_timed_out_pw_dump_reads_as_a_cli_error():
    with pytest.raises(pipewrench.CliError, match="timed out"):
        pipewrench._dump(lambda argv: (124, "pw-dump timed out after 10 s"))


def test_normal_commands_still_run():
    assert pipewrench.default_runner(["true"]) == (0, "")
