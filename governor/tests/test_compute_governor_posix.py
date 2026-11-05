"""Deterministic governor tests: no real probes, sleeps, or live state writes.

Run after integration with pytest governor/tests/test_compute_governor_posix.py.
For a staged copy set BRAIN_FRAMEWORK_ROOT to the checkout under test.
"""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def governor(monkeypatch, tmp_path):
    root = Path(os.environ.get("BRAIN_FRAMEWORK_ROOT", Path(__file__).resolve().parents[2]))
    spec = importlib.util.spec_from_file_location(
        "compute_governor_under_test", root / "governor" / "compute_governor_posix.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state_dir = tmp_path / "compute"
    monkeypatch.setattr(module, "HOME", str(tmp_path))
    monkeypatch.setattr(module, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(module, "STATE", str(state_dir / "compute_state.json"))
    monkeypatch.setattr(module, "STOP", str(state_dir / "STOP_GOVERNOR"))
    monkeypatch.setattr(module.os, "getloadavg", lambda: (1.234, 2.345, 3.456), raising=False)
    monkeypatch.setattr(module.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(module.socket, "gethostname", lambda: "fixture-host")
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=50_000_000_000))

    def no_subprocess(*args, **kwargs):
        pytest.fail("Unexpected real subprocess invocation")

    monkeypatch.setattr(module.subprocess, "run", no_subprocess)
    return module


@pytest.fixture
def probes(governor, monkeypatch):
    outputs = {
        "top": "CPU usage: 12.3% user, 4.5% sys, 83.2% idle\n",
        "memory_pressure": "System-wide memory free percentage: 61%\n",
        "sysctl": "total = 1024.00M  used = 128.50M  free = 895.50M (encrypted)\n",
        "ioreg": '    "HIDIdleTime" = 2000000000\n',
        "ps": " 100 12.5 2048 /usr/bin/python3\n 101 2.0 1024 /bin/bash\n",
    }
    calls = []

    def fake_sh(argv, timeout=10):
        calls.append((argv, timeout))
        assert argv[0] in outputs, "Unexpected probe command"
        return outputs[argv[0]]

    monkeypatch.setattr(governor, "sh", fake_sh)
    return outputs, calls


def test_sh_returns_stdout_and_passes_timeout(governor, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout="fixture output", returncode=9)

    monkeypatch.setattr(governor.subprocess, "run", run)
    assert governor.sh(["fake", "arg"], timeout=3) == "fixture output"
    assert calls == [(["fake", "arg"], dict(capture_output=True, text=True, timeout=3))]


@pytest.mark.parametrize("failure", [FileNotFoundError("absent"), OSError("denied"),
                                    __import__("subprocess").TimeoutExpired("fake", 1)])
def test_sh_probe_failures_return_empty(governor, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(governor.subprocess, "run", fail)
    assert governor.sh(["fake"]) == ""


def test_cpu_uses_last_top_sample(governor, probes):
    outputs, calls = probes
    outputs["top"] = ("CPU usage: 99.0% user, 1.0% sys, 0.0% idle\n"
                      "CPU usage: 12.3% user, 4.5% sys, 83.2% idle\n")
    assert governor.cpu_pct() == 16.8
    assert calls == [(["top", "-l", "2", "-n", "0", "-s", "1"], 15)]


@pytest.mark.parametrize("output", ["", "top: invalid option -- l", "malformed CPU usage"])
@pytest.mark.parametrize("cpus, expected", [(4, 30.9), (None, 123.4)])
def test_cpu_load_fallback(governor, probes, monkeypatch, output, cpus, expected):
    probes[0]["top"] = output
    monkeypatch.setattr(governor.os, "cpu_count", lambda: cpus)
    assert governor.cpu_pct() == expected


def test_cpu_load_unavailable(governor, probes, monkeypatch):
    probes[0]["top"] = ""

    def unavailable():
        raise OSError("load unavailable")

    monkeypatch.setattr(governor.os, "getloadavg", unavailable)
    assert governor.cpu_pct() is None


@pytest.mark.parametrize("pressure, swap, expected", [
    ("System-wide memory free percentage: 61%", "used = 128.50M", (61, 128.5)),
    ("free percentage: 0%", "used = 0.00M", (0, 0.0)),
    ("", "", (None, None)),
    ("unsupported", "used = unknown", (None, None)),
    ("free percentage: 100%", "", (100, None)),
])
def test_memory_parsing(governor, probes, pressure, swap, expected):
    probes[0].update(memory_pressure=pressure, sysctl=swap)
    assert governor.mem() == expected
    assert probes[1] == [(["memory_pressure", "-Q"], 10),
                         (["sysctl", "-n", "vm.swapusage"], 10)]


@pytest.mark.parametrize("output, expected", [
    ('"HIDIdleTime" = 1234567890', 1.2),
    ('"HIDIdleTime"=0', 0.0),
    ('"HIDIdleTime" = 600000000000', 600.0),
    ("", None), ("HIDIdleTime = invalid", None),
])
def test_idle_parsing(governor, probes, output, expected):
    probes[0]["ioreg"] = output
    assert governor.idle_s() == expected


def test_ps_filters_bad_rows_sorts_and_limits(governor, probes):
    probes[0]["ps"] = ("\n 3 1.5 2049 /usr/bin/low\n"
                       " 8 200.0 4096 /Applications/My App/worker\n"
                       " 9 15.0 1023 /usr/bin/mid\n"
                       "bad 3.0 1024 /bad/pid\n"
                       "4 nope 1024 /bad/cpu\n"
                       "5 1.0 nope /bad/rss\n"
                       "6 1.0 1024\n")
    rows, count = governor.top_procs(n=2)
    assert count == 3
    assert rows == [dict(pid=8, cpu_pct=200.0, rss_mb=4, name="worker"),
                    dict(pid=9, cpu_pct=15.0, rss_mb=0, name="mid")]
    assert probes[1] == [(["ps", "-axo", "pid=,%cpu=,rss=,comm="], 10)]


def test_ps_empty(governor, probes):
    probes[0]["ps"] = ""
    assert governor.top_procs() == ([], 0)


def test_cycle_publishes_complete_state_atomically(governor, probes, monkeypatch):
    replacements = []
    real_replace = governor.os.replace

    def replace(src, dst):
        assert Path(src).is_file()
        assert json.loads(Path(src).read_text())["status"] == "ok"
        replacements.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr(governor.os, "replace", replace)
    state = governor.cycle()
    assert json.loads(Path(governor.STATE).read_text()) == state
    assert replacements == [(governor.STATE + ".tmp", governor.STATE)]
    assert not Path(governor.STATE + ".tmp").exists()
    assert state.items() >= dict(host="fixture-host", status="ok", mode="owner_present_polite",
                                cpu_pct=16.8, mem_free_pct=61, swap_used_mb=128.5,
                                disk_free_gb=50.0, proc_count=2, owner_idle_s=2.0,
                                owner_away=False, owner_active=True, cpus=4).items()
    assert state["load"] == [1.23, 2.35, 3.46]
    assert state["reasons"] == []
    assert len(state["top"]) == 2
    assert state["governor"].endswith("(observe only)")
    assert isinstance(state["cycle_ms"], int) and state["cycle_ms"] >= 0
    assert len(state["ts"]) == 19
    assert {argv[0] for argv, _ in probes[1]} == {"top", "memory_pressure", "sysctl", "ioreg", "ps"}


@pytest.mark.parametrize("free, disk, status, reasons, mode", [
    (15, 10, "ok", [], "owner_present_polite"),
    (14, 10, "warn", ["memory_physical_pressure"], "owner_present_polite"),
    (5, 10, "warn", ["memory_physical_pressure"], "owner_present_polite"),
    (4, 10, "distress", ["memory_physical_critical"], "throttle"),
    (61, 9.9, "warn", ["disk_low"], "owner_present_polite"),
    (61, 3, "warn", ["disk_low"], "owner_present_polite"),
    (61, 2.9, "distress", ["disk_low"], "throttle"),
    (4, 9.9, "distress", ["memory_physical_critical", "disk_low"], "throttle"),
    (14, 2.9, "distress", ["memory_physical_pressure", "disk_low"], "throttle"),
])
def test_pressure_boundaries(governor, probes, monkeypatch, free, disk, status, reasons, mode):
    probes[0]["memory_pressure"] = f"free percentage: {free}%"
    monkeypatch.setattr(governor.shutil, "disk_usage", lambda _: SimpleNamespace(free=int(disk * 1e9)))
    state = governor.cycle()
    assert (state["status"], state["reasons"], state["mode"]) == (status, reasons, mode)


@pytest.mark.parametrize("idle, away, mode, reason", [
    (599, False, "throttle", "cpu_high_owner_present"),
    (600, True, "owner_away_open", "cpu_saturated"),
    (None, False, "throttle", "cpu_high_owner_present"),
])
def test_sustained_cpu_and_owner_boundary(governor, probes, idle, away, mode, reason):
    probes[0]["top"] = "CPU usage: 90.0% user, 5.0% sys"
    probes[0]["ioreg"] = "" if idle is None else f'"HIDIdleTime" = {idle * 10**9}'
    for _ in range(2):
        state = governor.cycle()
        assert state["status"] == "ok"
        assert state["mode"] == ("owner_away_open" if away else "owner_present_polite")
    state = governor.cycle()
    assert (state["status"], state["mode"], state["reasons"]) == ("warn", mode, [reason])
    assert state["owner_away"] is away
    assert state["owner_active"] is not away
    probes[0]["top"] = "CPU usage: 90.0% user, 4.9% sys"
    assert governor.cycle()["status"] == "ok"
    assert governor._sat_run == 0
    probes[0]["top"] = "CPU usage: 90.0% user, 5.0% sys"
    assert governor.cycle()["status"] == "ok"
    assert governor._sat_run == 1


def test_unknown_samples_reset_cpu_and_preserve_nulls(governor, probes, monkeypatch):
    probes[0].update(memory_pressure="", sysctl="", ioreg="", ps="")
    monkeypatch.setattr(governor, "cpu_pct", lambda: None)

    def disk_unavailable(_):
        raise OSError("unavailable")

    monkeypatch.setattr(governor.shutil, "disk_usage", disk_unavailable)
    governor._sat_run = 2
    state = governor.cycle()
    assert governor._sat_run == 0
    for key in ("cpu_pct", "mem_free_pct", "swap_used_mb", "owner_idle_s", "disk_free_gb"):
        assert state[key] is None
    assert (state["status"], state["mode"]) == ("ok", "owner_present_polite")


def test_distress_still_throttles_when_owner_away(governor, probes):
    probes[0].update(memory_pressure="free percentage: 4%", ioreg='"HIDIdleTime" = 700000000000')
    assert governor.cycle()["mode"] == "throttle"


@pytest.mark.parametrize("args", [[], ["--once"]])
def test_main_once(governor, probes, monkeypatch, capsys, args):
    monkeypatch.setattr(governor.sys, "argv", ["governor"] + args)
    assert governor.main() == 0
    assert "status=ok mode=owner_present_polite cpu=16.8% mem_free=61% reasons=[]" in capsys.readouterr().out
    assert Path(governor.STATE).is_file()
    assert not (Path(governor.STATE_DIR) / "governor.pid").exists()


@pytest.mark.parametrize("every, expected_sleep", [(1, 5), (60, 60)])
def test_loop_recovers_from_error_and_stops(governor, monkeypatch, capsys, every, expected_sleep):
    monkeypatch.setattr(governor.sys, "argv", ["governor", "--every", str(every)])
    cycles, sleeps = [], []

    def cycle():
        cycles.append(1)
        if len(cycles) == 1:
            raise OSError("fixture probe error")
        Path(governor.STOP).touch()

    monkeypatch.setattr(governor, "cycle", cycle)
    monkeypatch.setattr(governor.time, "sleep", sleeps.append)
    assert governor.main() == 0
    assert len(cycles) == 2
    assert sleeps == [expected_sleep, expected_sleep]
    assert (Path(governor.STATE_DIR) / "governor.pid").read_text() == str(os.getpid())
    assert "governor cycle failed: fixture probe error" in capsys.readouterr().err


def test_existing_stop_skips_loop(governor, monkeypatch):
    Path(governor.STOP).parent.mkdir()
    Path(governor.STOP).touch()
    monkeypatch.setattr(governor.sys, "argv", ["governor", "--every", "60"])
    monkeypatch.setattr(governor, "cycle", lambda: pytest.fail("STOP must prevent sampling"))
    monkeypatch.setattr(governor.time, "sleep", lambda _: pytest.fail("STOP must prevent sleeping"))
    assert governor.main() == 0
