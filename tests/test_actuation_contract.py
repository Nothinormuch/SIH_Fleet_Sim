"""Headless physics and the live vendor gate must agree on hard-stop semantics."""
from dataclasses import replace

import pytest

from src.amr import AMRBrain, POLICY_BIOS_PIBT_V7
from src.environment import open_floor
from src.settings import DEFAULT
from src.vendor_adapter import SafeCommandGate
from src.world import Actuation, World


def _fixture():
    env = open_floor(8, 8)
    world = World(env, DEFAULT)
    world.add_robot("A", (2, 2))
    other = world.add_robot("B", (3, 2))
    other.x = 4.4  # 0.20 m body clearance: inside omni, outside standstill margin.
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_BIOS_PIBT_V7)
    return brain, world


def test_validated_protective_turn_survives_unchanged_vendor_gate():
    brain, world = _fixture()
    sensors = world.sense("A")
    command = brain._safety(sensors, Actuation(0.0, 1.0))
    assert command.v == 0.0 and command.omega != 0.0 and not command.safety_stop
    gate = SafeCommandGate(DEFAULT.robot.v_max, DEFAULT.robot.omega_max)
    assert gate.accept(command, 0.0)
    assert gate.command(0.0) == command
    assert brain.stats["protective_turn_commands"] == 1
    assert gate.command(0.11) == Actuation(safety_stop=True)


@pytest.mark.parametrize("hazard", ["momentum", "closing-person", "contact-envelope", "explicit-stop"])
def test_turn_is_not_a_bypass_for_hard_stop_conditions(hazard):
    brain, world = _fixture()
    sensors = world.sense("A")
    request = Actuation(0.0, 1.0)
    if hazard == "momentum":
        sensors = replace(sensors, v=.08)
    elif hazard == "closing-person":
        sensors = replace(sensors, detections=[replace(d, vx=-1.0) for d in sensors.detections])
    elif hazard == "contact-envelope":
        sensors = replace(sensors, clearance_omni_m=0.0)
    else:
        request = Actuation(0.0, 1.0, safety_stop=True)
    assert brain._safety(sensors, request) == Actuation(safety_stop=True)


def test_world_and_gate_both_brake_both_axes_for_flagged_stop():
    _, direct = _fixture()
    _, gated = _fixture()
    gate = SafeCommandGate(DEFAULT.robot.v_max, DEFAULT.robot.omega_max)
    commands = [Actuation(.2, .3)] * 4 + [Actuation(.5, .5, safety_stop=True)] * 20
    for index, command in enumerate(commands):
        gate.accept(command, index * .02)
        direct.step(.02, {"A": command})
        gated.step(.02, {"A": gate.command(index * .02)})
        assert direct.robots["A"] == gated.robots["A"]
    assert direct.robots["A"].v == 0.0 and direct.robots["A"].omega == 0.0
    assert direct.robots["A"].safety_stops == 20


def test_explicit_stop_cannot_be_reclassified_as_separating_creep():
    brain, world = _fixture()
    sensors = replace(world.sense("A"), pose=(3.5, 3.5, 3.141592653589793))
    assert brain._escape_motion_increases_clearance(sensors, Actuation(.2, 0.0))
    assert brain._safety(sensors, Actuation(.2, .5, safety_stop=True)) == Actuation(safety_stop=True)


def test_protective_turn_keeps_translation_stall_but_ordinary_turn_releases_it(monkeypatch):
    brain, world = _fixture()
    brain.goal = (3, 2)
    # Isolate control-command semantics from task selection and route scheduling.
    for phase in ("_task_loop", "_route_loop", "_traffic_loop"):
        monkeypatch.setattr(brain, phase, lambda *args: None)
    monkeypatch.setattr(brain, "_follow", lambda *args: Actuation(0.0, 1.0))
    sensors = world.sense("A")
    for at in (1.0, 1.1, 1.2):
        command, _ = brain.step(at, replace(sensors, t=at), [])
        assert not command.safety_stop and command.omega != 0.0
        assert brain._translation_protected
        assert brain._stall_since == 1.0
    assert brain.stats["nonproductive_wait_ticks"] == 3
    world.robots["B"].x = 8.0
    command, _ = brain.step(1.3, replace(world.sense("A"), t=1.3), [])
    assert not command.safety_stop and command.omega != 0.0
    assert not brain._translation_protected
    assert brain._stall_since is None
