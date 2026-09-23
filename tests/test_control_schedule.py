"""A best-effort hint must stay scoped and must never become a timing pass flag."""
from types import SimpleNamespace
import time

import pytest

from src import control_schedule as schedule
from src import edge_runtime


class Function:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def _library(monkeypatch, *, token=123, release=0):
    lib = SimpleNamespace(pthread_self=Function(42),
        pthread_override_qos_class_start_np=Function(token),
        pthread_override_qos_class_end_np=Function(release))
    monkeypatch.setattr(schedule.sys, "platform", "darwin")
    monkeypatch.setattr(schedule.ctypes, "CDLL", lambda *args, **kwargs: lib)
    return lib


@pytest.mark.parametrize("raises", [False, True])
def test_darwin_hint_only_targets_current_thread_and_is_released(monkeypatch, raises):
    lib = _library(monkeypatch)
    hint = schedule.ControlThreadQoS()
    try:
        with hint:
            assert hint.report()["applied"]
            assert not hint.report()["hard_real_time"]
            if raises:
                raise ValueError("controller failure")
    except ValueError:
        assert raises
    assert lib.pthread_override_qos_class_start_np.calls == [(42, 0x19, 0)]
    assert lib.pthread_override_qos_class_end_np.calls == [(123,)]
    assert hint.report()["released"] is True


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_other_hosts_keep_their_existing_scheduler(monkeypatch, platform):
    monkeypatch.setattr(schedule.sys, "platform", platform)
    monkeypatch.setattr(schedule.ctypes, "CDLL", lambda *a, **k: pytest.fail("native API called"))
    with schedule.ControlThreadQoS() as hint:
        assert hint.report()["mode"] == "platform_default"
        assert not hint.report()["applied"]


def test_native_api_rejection_is_visible_not_a_claimed_hint(monkeypatch):
    lib = _library(monkeypatch, token=None)
    with schedule.ControlThreadQoS() as hint:
        assert not hint.report()["applied"] and hint.report()["error"]
    assert lib.pthread_override_qos_class_end_np.calls == []


def test_release_failure_is_reported(monkeypatch):
    _library(monkeypatch, release=22)
    with schedule.ControlThreadQoS() as hint:
        pass
    assert hint.report()["released"] is False and hint.report()["error"]


def test_unavailable_api_keeps_normal_execution(monkeypatch):
    monkeypatch.setattr(schedule.sys, "platform", "darwin")
    monkeypatch.setattr(schedule.ctypes, "CDLL", lambda *a, **k: SimpleNamespace())
    with schedule.ControlThreadQoS() as hint:
        assert not hint.report()["applied"] and hint.report()["error"]


def test_hint_does_not_override_controller_results(monkeypatch):
    _library(monkeypatch)
    monkeypatch.setattr(edge_runtime, "_run_edge_node_loop", lambda *a: {"success": False})
    report = edge_runtime.run_edge_node(None, None, None)
    assert report["success"] is False
    assert report["scheduling_hint"]["released"]


def test_bridge_and_referee_hints_preserve_failures_and_arguments(monkeypatch):
    from src import multihost_demo as demo
    _library(monkeypatch)
    config, key, callback = {}, b"test-key", object()
    calls = []

    def agent(c, host, k, *, connect_wait_s):
        calls.append((c, host, k, connect_wait_s))
        return {"failure": "example failure"}

    def referee(c, k, timeout, snapshot):
        calls.append((c, k, timeout, snapshot))
        return {"success": False, "control_deadlines_met": False}

    monkeypatch.setattr(demo, "_run_agent_loop", agent)
    monkeypatch.setattr(demo, "_run_referee_loop", referee)
    assert demo.run_agent(config, "mac", key, connect_wait_s=45)["failure"]
    result = demo.run_referee(config, key, 60, callback)
    assert result["success"] is False and result["control_deadlines_met"] is False
    assert result["scheduling_hint"]["released"]
    assert calls == [(config, "mac", key, 45), (config, key, 60, callback)]


def test_timing_witness_storage_is_bounded_and_report_is_detached():
    from src.amr import AMRBrain
    from src.environment import open_floor
    from src.settings import DEFAULT
    transport = SimpleNamespace(stats={})
    runtime = edge_runtime.EdgeRuntime(AMRBrain("A", open_floor(8, 8), DEFAULT), transport)
    for index in range(100):
        runtime.record_timing_event("wake_late", float(index), .031)
    report = runtime.report()
    assert len(report["timing_events"]) == 16
    assert report["timing_events"][0]["delay_ms"] == 31.0
    report["timing_events"][0]["kind"] = "changed"
    assert runtime.timing_events[0]["kind"] == "wake_late"


@pytest.mark.parametrize("phase", ["sensor_read", "actuator_write", "journal_flush"])
def test_io_delay_remains_a_failed_full_cycle_and_identifies_phase(monkeypatch, phase):
    from src.amr import AMRBrain
    from src.environment import open_floor
    from src.settings import DEFAULT
    from src.world import World
    env = open_floor(8, 8)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("A", (1, 1))
    brain = AMRBrain("A", env, DEFAULT, home=(1, 1))
    transport = SimpleNamespace(stats={}, poll=lambda: [], send=lambda m: None,
                                close=lambda: None)

    def delay(name):
        if name == phase:
            time.sleep(.030)

    class Hardware:
        stats = {}

        def read_sensors(self):
            delay("sensor_read")
            return world.sense("A"), time.monotonic()

        def write_actuation(self, actuation, timestamp):
            delay("actuator_write")

        def close(self):
            pass

    monkeypatch.setattr(edge_runtime.EdgeRuntime, "flush_terminal_records",
                        lambda self: delay("journal_flush"))
    report = edge_runtime.run_edge_node(brain, transport, Hardware(), duration_s=.035,
                                        startup_sensor_wait_s=0)
    assert report["full_cycle"]["deadline_misses"] > 0
    assert report["phase_max_ms"][phase] >= 30
    assert any(e["kind"] == phase and e["delay_ms"] >= 30 for e in report["timing_events"])
