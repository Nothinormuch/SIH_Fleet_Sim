"""Closed-loop deployment socket proof."""

import socket

from src.hil_demo import run_hil_demo
from src.task_allocation import ALLOCATION_PREASSIGNED


def _free_udp_range(size: int) -> int:
    for base in range(31_000, 60_000, size + 1):
        sockets = []
        try:
            for port in range(base, base + size):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("127.0.0.1", port))
                sockets.append(sock)
            return base
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("could not find a free UDP port range")


def test_public_edge_nodes_close_loop_through_udp_hardware_contract():
    base = _free_udp_range(7)
    peer_port = base
    sensor_base = base + 1
    actuator_base = base + 4
    result = run_hil_demo(
        robots=3,
        duration_s=0.6,
        allocation_policy=ALLOCATION_PREASSIGNED,
        peer_port=peer_port,
        sensor_base_port=sensor_base,
        actuator_base_port=actuator_base,
    )

    assert result["success"]
    assert result["proof_scope"] == "closed_loop_software_in_the_loop"
    assert result["controller_boundary_exercised"]
    assert result["separate_edge_nodes"]
    assert result["peer_messages_observed"]
    assert result["authenticated_transport"]
    assert result["control_deadlines_met"]
    assert not result["physical_amr_tested"]
    assert result["contacts"]["robot-robot"] == 0
    assert all(node["hardware"]["sensor_frames"] > 0 for node in result["nodes"])
    assert all(node["hardware"]["actuator_frames"] > 0 for node in result["nodes"])


def test_sensor_cut_produces_bounded_fail_safe_stop_and_recovers():
    base = _free_udp_range(7)
    result = run_hil_demo(
        robots=3,
        duration_s=1.4,
        allocation_policy=ALLOCATION_PREASSIGNED,
        peer_port=base,
        sensor_base_port=base + 1,
        actuator_base_port=base + 4,
        sensor_cut_robot="AMR01",
        sensor_cut_at_s=0.3,
        sensor_cut_duration_s=0.5,
    )

    assert result["success"]
    assert result["sensor_cut_evidence"]["pass"]
    assert result["sensor_cut_evidence"]["response_s"] <= 0.4
    assert result["sensor_cut_evidence"]["recovered_after_sensor_return"]
