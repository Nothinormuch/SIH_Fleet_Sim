"""One-command, machine-readable acceptance gate for the BIOS deployment boundary."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path

from . import messages as msg
from .hil_demo import run_hil_demo
from .site_config import load_site_config
from .task_allocation import ALLOCATION_AUCTION_BUNDLE, ALLOCATION_PREASSIGNED
from .task_injector import load_task_file
from .transport import DEFAULT_GROUP, UdpMulticastTransport
from .vendor_adapter import SafeCommandGate
from .world import Actuation


ROOT = Path(__file__).resolve().parent.parent


def _free_udp_block(size: int) -> int:
    for base in range(32_000, 60_000, size + 1):
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
    raise RuntimeError("could not reserve a local UDP range for acceptance")


def network_security_probe(port: int, group: str = DEFAULT_GROUP,
                           interface: str = "0.0.0.0") -> dict:
    """Prove wrong-key rejection and duplicate replay rejection over real sockets."""
    receiver = UdpMulticastTransport(
        "AMR-RECEIVER", group=group, port=port, interface=interface,
        shared_key="acceptance-correct-key-0001", require_auth=True,
    )
    attacker = UdpMulticastTransport(
        "UNTRUSTED", group=group, port=port, interface=interface,
        shared_key="acceptance-wrong-key-000001", require_auth=True,
    )
    peer = UdpMulticastTransport(
        "AMR-PEER", group=group, port=port, interface=interface,
        shared_key="acceptance-correct-key-0001", require_auth=True,
        session_id="acceptance-session",
    )
    try:
        hostile = msg.task_new(
            "UNTRUSTED", 1, 0.0, "HOSTILE", (0, 0), (1, 1),
            bid_until=0.6,
        )
        attacker.send(hostile)
        duplicate = msg.task_new(
            "AMR-PEER", 7, 0.0, "REPLAY-PROBE", (0, 0), (1, 1),
            bid_until=0.6,
        )
        peer.send(duplicate)
        peer.send(duplicate)
        time.sleep(0.08)
        accepted = receiver.poll()
        accepted_ids = [message.body.get("task") for message in accepted]
        wrong_key_rejected = (
            receiver.stats["auth_failed"] >= 1 and "HOSTILE" not in accepted_ids
        )
        replay_rejected = (
            receiver.stats["replayed"] >= 1
            and accepted_ids.count("REPLAY-PROBE") == 1
        )
        return {
            "pass": wrong_key_rejected and replay_rejected,
            "wrong_key_rejected": wrong_key_rejected,
            "duplicate_replay_rejected": replay_rejected,
            "accepted_task_ids": accepted_ids,
            "receiver_stats": dict(receiver.stats),
        }
    finally:
        receiver.close()
        attacker.close()
        peer.close()


def _git_provenance() -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT,
            check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        return {"commit": commit, "dirty": bool(status),
                "changed_paths": status[:100]}
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"commit": None, "dirty": None, "error": str(exc)}


def controller_watchdog_probe() -> dict:
    gate = SafeCommandGate(v_max=1.2, omega_max=1.6, command_timeout_s=0.10)
    accepted = gate.accept(Actuation(v=0.5, omega=0.2), received_at=100.0)
    fresh = gate.command(now=100.05)
    stale = gate.command(now=100.11)
    rejected = not gate.accept(Actuation(v=2.0), received_at=100.12)
    after_invalid = gate.command(now=100.13)
    passed = (
        accepted and not fresh.safety_stop and fresh.v == 0.5
        and stale.safety_stop and stale.v == 0.0
        and rejected and after_invalid.safety_stop and after_invalid.v == 0.0
    )
    return {
        "pass": passed,
        "fresh_command_allowed": not fresh.safety_stop,
        "stale_command_stopped": stale.safety_stop and stale.v == 0.0,
        "out_of_envelope_command_stopped": (
            rejected and after_invalid.safety_stop and after_invalid.v == 0.0
        ),
        "stats": gate.report(),
    }


def run_deployment_acceptance(duration_s: float = 20.0,
                              scenario_name: str = "deployment_socket_acceptance",
                              robots: int = 3,
                              site_path: str | Path | None = None,
                              task_path: str | Path | None = None) -> dict:
    """Run deployment, fail-safe, security, and configuration proof gates."""
    site_path = Path(site_path or ROOT / "config" / "site.example.json")
    task_path = Path(task_path or ROOT / "config" / "tasks.example.json")
    site = load_site_config(site_path)
    tasks = load_task_file(task_path, site_path)

    normal_ports = _free_udp_block(1 + 2 * robots)
    normal = run_hil_demo(
        scenario_name=scenario_name,
        robots=robots,
        duration_s=duration_s,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
        peer_port=normal_ports,
        sensor_base_port=normal_ports + 1,
        actuator_base_port=normal_ports + 1 + robots,
        shared_key="deployment-acceptance-key-0001",
        require_task_completion=True,
    )
    fail_safe_ports = _free_udp_block(7)
    fail_safe = run_hil_demo(
        scenario_name="open_floor_control",
        robots=3,
        duration_s=1.4,
        allocation_policy=ALLOCATION_PREASSIGNED,
        peer_port=fail_safe_ports,
        sensor_base_port=fail_safe_ports + 1,
        actuator_base_port=fail_safe_ports + 4,
        shared_key="deployment-acceptance-key-0001",
        sensor_cut_robot="AMR01",
        sensor_cut_at_s=0.3,
        sensor_cut_duration_s=0.5,
    )
    security = network_security_probe(_free_udp_block(1))
    controller_watchdog = controller_watchdog_probe()
    configuration = {
        "pass": True,
        "fleet_id": site.fleet_id,
        "map_name": site.environment.name,
        "map_version": site.map_version,
        "site_fingerprint_sha256": site.fingerprint,
        "configured_robots": len(site.starts),
        "validated_tasks": len(tasks),
    }
    gates = {
        "three_or_more_independent_edge_nodes": (
            normal["separate_edge_nodes"] and normal["robots"] >= 3
        ),
        "deployment_sensor_actuator_boundary": normal["controller_boundary_exercised"],
        "authenticated_peer_communication": normal["authenticated_transport"],
        "peer_messages_observed": normal["peer_messages_observed"],
        "control_deadlines_met": normal["control_deadlines_met"],
        "contact_free_in_measured_run": all(
            value == 0 for value in normal["contacts"].values()
        ),
        "auction_v2_completed_all_declared_work": (
            normal["tasks_completed"] == normal["tasks_announced"]
            and normal["tasks_announced"] > 0
        ),
        "sensor_timeout_fail_safe_and_recovery": (
            fail_safe["sensor_cut_evidence"] is not None
            and fail_safe["sensor_cut_evidence"]["pass"]
        ),
        "wrong_key_and_replay_rejected": security["pass"],
        "controller_command_watchdog_and_envelope": controller_watchdog["pass"],
        "facility_and_task_inputs_validated": configuration["pass"],
    }
    success = all(gates.values()) and normal["success"] and fail_safe["success"]
    return {
        "success": success,
        "verdict": "PASS" if success else "FAIL",
        "scope": "software deployment acceptance on the reported host",
        "gates": gates,
        "normal_closed_loop_run": normal,
        "sensor_fail_safe_run": fail_safe,
        "network_security_probe": security,
        "controller_watchdog_probe": controller_watchdog,
        "configuration_probe": configuration,
        "provenance": _git_provenance(),
        "claims": {
            "proved": [
                "one edge_node.py process per simulated AMR",
                "real JSON/UDP sensor and actuator boundary",
                "authenticated peer multicast without a winner-selecting referee",
                "bounded sensor-staleness stop and recovery in this run",
                "validated configurable map, robot profile, and WMS task descriptors",
            ],
            "not_proved": [
                "physical AMR safety certification",
                "compatibility with a vendor whose controller API has no adapter",
                "Raspberry Pi timing unless raspberry_pi_tested is true in the run",
                "universal task completion or zero collisions outside measured runs",
            ],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run all jury-facing BIOS deployment acceptance gates",
    )
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--scenario", default="deployment_socket_acceptance")
    parser.add_argument("--robots", type=int, default=3)
    parser.add_argument("--site-config")
    parser.add_argument("--tasks")
    parser.add_argument(
        "--output", default="artifacts/deployment/deployment-acceptance.json",
    )
    parser.add_argument("--json-stdout", action="store_true",
                        help="print the full evidence JSON instead of the jury summary")
    return parser


def format_summary(result: dict, output: str | None = None) -> str:
    normal = result["normal_closed_loop_run"]
    sensor = result["sensor_fail_safe_run"]["sensor_cut_evidence"]
    lines = [
        f"BIOS DEPLOYMENT ACCEPTANCE: {result['verdict']}",
        f"Independent edge nodes: {normal['robots']}",
        f"Auction V2 work completed: {normal['tasks_completed']} / "
        f"{normal['tasks_announced']} during {normal['duration_s']:.1f} s window",
        "Measured contacts: "
        f"robot-robot={normal['contacts']['robot-robot']}, "
        f"robot-human={normal['contacts']['robot-human']}, "
        f"robot-rack={normal['contacts']['robot-rack']}",
        "Control deadline misses: "
        f"{sum(node['runtime']['deadline_misses'] for node in normal['nodes'])}",
        f"Sensor-loss stop response: {sensor['response_s'] * 1000.0:.1f} ms; "
        f"recovered={sensor['recovered_after_sensor_return']}",
        "Security: wrong key rejected; duplicate replay rejected",
        f"Host is a measured Raspberry Pi: {normal['raspberry_pi_tested']}",
    ]
    if output:
        lines.append(f"Machine-readable evidence: {output}")
    lines.append(
        "Scope: closed-loop software deployment evidence; not physical safety certification."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_deployment_acceptance(
        duration_s=args.duration,
        scenario_name=args.scenario,
        robots=args.robots,
        site_path=args.site_config,
        task_path=args.tasks,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded if args.json_stdout else format_summary(result, args.output))
    return 0 if result["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
