"""Controller-facing fail-safe gate."""

from src.vendor_adapter import SafeCommandGate
from src.world import Actuation


def test_command_gate_times_out_to_stop_and_recovers_on_fresh_frame():
    gate = SafeCommandGate(v_max=1.2, omega_max=1.6, command_timeout_s=0.1)

    assert gate.accept(Actuation(v=0.7, omega=0.3), received_at=10.0)
    assert gate.command(now=10.09).v == 0.7
    assert gate.command(now=10.11).safety_stop
    assert gate.command(now=10.11).v == 0.0
    assert gate.report()["watchdog_stops"] == 1

    assert gate.accept(Actuation(v=0.4), received_at=10.12)
    assert not gate.command(now=10.13).safety_stop


def test_command_gate_rejects_out_of_envelope_and_zeroes_stop_frames():
    gate = SafeCommandGate(v_max=1.0, omega_max=1.0)

    assert not gate.accept(Actuation(v=1.01), received_at=0.0)
    assert gate.command(now=0.01).safety_stop
    assert gate.accept(
        Actuation(v=0.5, omega=0.5, safety_stop=True), received_at=0.02,
    )
    stopped = gate.command(now=0.03)
    assert stopped.safety_stop
    assert stopped.v == 0.0
    assert stopped.omega == 0.0
