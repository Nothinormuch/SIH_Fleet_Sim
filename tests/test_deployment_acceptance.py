"""Security portions of the one-command deployment acceptance gate."""

import socket

from src.deployment_acceptance import (controller_watchdog_probe, format_summary,
                                       network_security_probe)


def _available_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def test_real_socket_probe_rejects_wrong_key_and_duplicate_replay():
    result = network_security_probe(_available_udp_port())

    assert result["pass"]
    assert result["wrong_key_rejected"]
    assert result["duplicate_replay_rejected"]
    assert "HOSTILE" not in result["accepted_task_ids"]
    assert result["accepted_task_ids"].count("REPLAY-PROBE") == 1


def test_controller_watchdog_probe_stops_stale_and_invalid_commands():
    result = controller_watchdog_probe()

    assert result["pass"]
    assert result["fresh_command_allowed"]
    assert result["stale_command_stopped"]
    assert result["out_of_envelope_command_stopped"]


def test_jury_summary_keeps_scope_and_result_visible():
    result = {
        "verdict": "PASS",
        "normal_closed_loop_run": {
            "robots": 3, "tasks_completed": 2, "tasks_announced": 12,
            "duration_s": 20.0,
            "contacts": {"robot-robot": 0, "robot-human": 0, "robot-rack": 0},
            "nodes": [{"runtime": {"deadline_misses": 0}}] * 3,
            "raspberry_pi_tested": False,
        },
        "sensor_fail_safe_run": {"sensor_cut_evidence": {
            "response_s": 0.28, "recovered_after_sensor_return": True,
        }},
    }

    summary = format_summary(result, "evidence.json")
    assert "ACCEPTANCE: PASS" in summary
    assert "280.0 ms" in summary
    assert "not physical safety certification" in summary
    assert "evidence.json" in summary
