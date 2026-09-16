"""Fail-safe command gate shared by HIL and future vendor-specific AMR adapters."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .world import Actuation


@dataclass
class CommandGateStats:
    accepted: int = 0
    safety_stops: int = 0
    invalid_commands: int = 0
    watchdog_stops: int = 0


class SafeCommandGate:
    """Allow motion only while valid BIOS commands arrive inside a short deadline.

    This belongs on the controller-facing side of the adapter. If the Pi process,
    network socket, or adapter fails, the gate returns a positive stop command instead
    of replaying the last velocity forever. A real AMR should enforce an equivalent or
    stronger watchdog inside its vendor controller as well.
    """

    def __init__(self, v_max: float, omega_max: float,
                 command_timeout_s: float = 0.10) -> None:
        if not math.isfinite(v_max) or v_max <= 0.0:
            raise ValueError("v_max must be positive and finite")
        if not math.isfinite(omega_max) or omega_max <= 0.0:
            raise ValueError("omega_max must be positive and finite")
        if not math.isfinite(command_timeout_s) or command_timeout_s <= 0.0:
            raise ValueError("command_timeout_s must be positive and finite")
        self.v_max = v_max
        self.omega_max = omega_max
        self.command_timeout_s = command_timeout_s
        self.stats = CommandGateStats()
        self._command = Actuation(safety_stop=True)
        self._received_at: float | None = None
        self._watchdog_active = False

    def accept(self, command: Actuation, received_at: float | None = None) -> bool:
        now = time.monotonic() if received_at is None else received_at
        valid = (
            math.isfinite(command.v)
            and math.isfinite(command.omega)
            and abs(command.v) <= self.v_max + 1e-9
            and abs(command.omega) <= self.omega_max + 1e-9
        )
        if not valid:
            self.stats.invalid_commands += 1
            self._command = Actuation(safety_stop=True)
            self._received_at = now
            self._watchdog_active = False
            return False
        if command.safety_stop:
            # Never pass a non-zero command with a stop flag to a vendor controller.
            self._command = Actuation(v=0.0, omega=0.0, safety_stop=True)
            self.stats.safety_stops += 1
        else:
            self._command = Actuation(command.v, command.omega, False)
        self._received_at = now
        self._watchdog_active = False
        self.stats.accepted += 1
        return True

    def command(self, now: float | None = None) -> Actuation:
        current = time.monotonic() if now is None else now
        stale = (
            self._received_at is None
            or current - self._received_at > self.command_timeout_s
        )
        if stale:
            if not self._watchdog_active:
                self.stats.watchdog_stops += 1
                self._watchdog_active = True
            return Actuation(v=0.0, omega=0.0, safety_stop=True)
        return self._command

    def report(self) -> dict:
        return {
            "command_timeout_s": self.command_timeout_s,
            "accepted": self.stats.accepted,
            "safety_stops": self.stats.safety_stops,
            "invalid_commands": self.stats.invalid_commands,
            "watchdog_stops": self.stats.watchdog_stops,
        }
