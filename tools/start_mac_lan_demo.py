"""Operator-friendly Mac launcher; does not change the pinned controller source.

Wait for the remote TCP connection before starting local edge controllers. Their
finite watchdog lifetime must not be consumed while a person copies a command.
Connection detection is only a launch trigger: the referee still authenticates
both agents and verifies every controller before announcing any work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def remote_connected(output: str, local_ip: str, port: int, remote_ip: str) -> bool:
    """Match only this listener's established incoming IPv4 connection."""
    prefix = f"{local_ip}:{port}->{remote_ip}:"
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[-1] == "(ESTABLISHED)":
            endpoint = fields[-2]
            if endpoint.startswith(prefix) and endpoint[len(prefix):].isdigit():
                return True
    return False


def stop_child(process: subprocess.Popen | None) -> bool:
    """Clean an owned, isolated process group, including abandoned controllers.

    Both callers use start_new_session=True, so the child's PID is its group ID.
    Interrupt the supervisor first for normal evidence collection, then clean any
    remaining descendants. Never signal the launcher's or an external process group.
    """
    if process is None:
        return True
    clean = True
    try:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            # Five nodes can each require 5+3 seconds of graceful/fallback cleanup.
            process.wait(timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        clean = False
    finally:
        try:
            # If the supervisor exited abnormally, its children can still own this
            # session. A bounded group fallback prevents orphaned AMR processes.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            clean = False
            print(f"Cannot clean owned process group {process.pid}: {exc}", flush=True)
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            clean = False
            print(f"Cannot reap owned process {process.pid}: {exc}", flush=True)
    return clean


def run_owned_session(*, lsof: str, local_ip: str, remote_ip: str, port: int,
                      common: list[str], session: list[str], output: Path,
                      ready_timeout: int, duration: float) -> int:
    """Return success only after both supervisors and their cleanup succeed."""
    referee = agent = None
    code = 1
    try:
        referee = subprocess.Popen(
            common + ["referee"] + session + ["--ready-timeout", str(ready_timeout),
                                              "--output", str(output / "referee.json")],
            cwd=ROOT, start_new_session=True)
        print(f"Evidence directory: {output}", flush=True)
        print(f"Waiting up to {ready_timeout}s for Lenovo {remote_ip}. "
              "Local controllers start only after its connection arrives.", flush=True)
        deadline = time.monotonic() + ready_timeout + 10
        while time.monotonic() < deadline:
            referee_code = referee.poll()
            if referee_code is not None:
                print(f"Referee exited before Lenovo connected (exit {referee_code}).", flush=True)
                break
            connections = subprocess.run(
                [lsof, "-nP", "-a", "-p", str(referee.pid), f"-iTCP:{port}",
                 "-sTCP:ESTABLISHED"], capture_output=True, text=True, timeout=2)
            if remote_connected(connections.stdout, local_ip, port, remote_ip):
                print("Lenovo TCP connection observed; starting Mac controllers. "
                      "Authentication and readiness checks still apply.", flush=True)
                agent = subprocess.Popen(
                    common + ["agent"] + session + ["--host", "mac", "--output",
                                                    str(output / "agent-mac.json")],
                    cwd=ROOT, start_new_session=True)
                referee_code = referee.wait(timeout=duration + 90)
                agent_code = agent.wait(timeout=20)
                print(f"Referee exit={referee_code}; Mac agent exit={agent_code}. "
                      f"Read measured evidence in {output}", flush=True)
                code = 0 if referee_code == agent_code == 0 else 1
                break
            time.sleep(0.1)
        else:
            print("Remote connection wait expired; no successful demo is claimed.", flush=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Launcher failed: {type(exc).__name__}: {exc}", flush=True)
    except KeyboardInterrupt:
        print("Launcher interrupted; stopping only its own demo processes.", flush=True)
        code = 130
    finally:
        # Do not short-circuit: both groups must be cleaned even if the first fails.
        referee_clean = stop_child(referee)
        agent_clean = stop_child(agent)
        if not referee_clean or not agent_clean:
            code = 130 if code == 130 else 1
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--ready-timeout", type=int, default=1800)
    parser.add_argument("--output-dir", type=Path,
                        help="New directory for raw reports; an existing path is refused")
    args = parser.parse_args(argv)
    lsof = shutil.which("lsof")
    if sys.platform != "darwin" or not lsof:
        parser.error("This launcher requires macOS and lsof; agents remain cross-platform.")
    if not 30 <= args.ready_timeout <= 3600:
        parser.error("--ready-timeout must be between 30 and 3600 seconds")
    config_path, key_path = Path(args.config).resolve(), Path(args.key_file).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not key_path.is_file():
        parser.error("Session key file is missing")
    local_ip = str(ipaddress.IPv4Address(config["referee_ip"]))
    remote_ip = str(ipaddress.IPv4Address(config["hosts"]["windows"]["ip"]))
    port = int(config["bridge_port"])
    if local_ip == remote_ip or not 1 <= port <= 65535:
        parser.error("Use distinct Mac/Windows IPv4 addresses and a valid bridge port")

    # Preflight without reading/printing the secret; do not bypass the normal
    # startup checks in either of the processes launched below.
    sys.path.insert(0, str(ROOT))
    from src.multihost_demo import validate_config
    validate_config(config)
    evidence_root = ROOT / "artifacts" / "deployment"
    evidence_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.output_dir is None:
        output = Path(tempfile.mkdtemp(prefix=f"lan-{stamp}-", dir=evidence_root))
    else:
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=False)
    common = [sys.executable, str(ROOT / "multihost_demo.py")]
    session = ["--config", str(config_path), "--key-file", str(key_path)]
    return run_owned_session(lsof=lsof, local_ip=local_ip, remote_ip=remote_ip,
                             port=port, common=common, session=session, output=output,
                             ready_timeout=args.ready_timeout, duration=float(config["duration_s"]))


if __name__ == "__main__":
    raise SystemExit(main())
