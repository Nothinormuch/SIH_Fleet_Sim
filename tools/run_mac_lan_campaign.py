"""Run the Mac side of a pinned LAN campaign; retain every attempted result.

Windows runs `multihost_demo.py campaign-agent` with the same manifest. Neither
launcher allocates work or relays robot peer packets. Experimental success is
determined by the strict referee reports, never a controller's process exit alone.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.multihost_demo import load_campaign  # noqa: E402
from tools.start_mac_lan_demo import main as run_session  # noqa: E402


def campaign_summary(rows: list[dict], expected: int) -> dict:
    return {
        "scope": "networked_software_in_the_loop",
        "physical_amr_tested": False,
        "expected_sessions": expected,
        "attempted_sessions": len(rows),
        "unrun_sessions": expected - len(rows),
        "success": len(rows) == expected and expected > 0 and all(
            row.get("success") is True and row.get("launcher_exit") == 0 for row in rows),
        "sessions": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    # All configurations, source/workload pins and private paths are validated
    # before starting any process. Do not print the rows: they contain private keys.
    entries = load_campaign(args.manifest, "mac")
    evidence_root = ROOT / "artifacts" / "deployment"
    evidence_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"lan-campaign-{stamp}-", dir=evidence_root))
    summary_path = output / "campaign.json"
    results = []

    def save():
        summary_path.write_text(json.dumps(campaign_summary(results, len(entries)),
                                          indent=2, sort_keys=True) + "\n", encoding="utf-8")

    save()
    print(f"Campaign evidence: {summary_path}", flush=True)
    for index, entry in enumerate(entries, 1):
        config = entry["configuration"]
        target = output / f"{index:02d}-{config['scenario']}-{config['robots']}"
        print(f"Mac session {index}/{len(entries)}: {config['scenario']} "
              f"({config['robots']} AMRs)", flush=True)
        row = {"index": index, "session": config["session"], "scenario": config["scenario"],
               "robots": config["robots"], "report": str(target / "referee.json")}
        try:
            row["launcher_exit"] = run_session([
                "--config", str(entry["config"]), "--key-file", str(entry["key_file"]),
                "--ready-timeout", str(int(config.get("readiness_timeout_s", 120))),
                "--output-dir", str(target)])
            report = json.loads((target / "referee.json").read_text(encoding="utf-8"))
            row.update({key: report.get(key) for key in (
                "success", "software_boundary_pass", "tasks_completed", "tasks_announced",
                "contacts", "control_deadlines_met", "real_multihost", "failure")})
            if report.get("config", {}).get("session") != config["session"]:
                raise ValueError("referee report does not match the requested session")
        except (OSError, ValueError, SystemExit) as exc:
            row.update(success=False, failure=f"{type(exc).__name__}: {exc}")
            results.append(row)
            save()
            print("Campaign stopped: no valid session result. Remaining sessions are unrun.", flush=True)
            return 1
        results.append(row)
        save()
        host_reports = report.get("host_reports", {})
        host_lifecycle_failed = any(host not in host_reports or
            host_reports[host].get("failure") or host_reports[host].get("missing")
            for host in config["hosts"])
        if host_lifecycle_failed or row["launcher_exit"] == 130:
            print("Campaign stopped after a host failure/interruption. "
                  "Remaining sessions are unrun.", flush=True)
            return 1
        # A measured performance/timing/safety failure remains a failure. Other
        # declared sessions may still supply useful evidence; there are no reruns.
    passed = campaign_summary(results, len(entries))["success"]
    print(f"LAN CAMPAIGN: {'PASS' if passed else 'FAIL'}; evidence: {summary_path}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
