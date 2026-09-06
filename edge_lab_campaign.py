"""Exercise the live lab HTTP boundary; keep its browser page open to add UI load."""
import argparse
import json
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--output", default="artifacts/deployment/edge-live-ui-campaign.json")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}/api/edge-lab/"
    def call(action, payload=None):
        request = urllib.request.Request(base + action,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    if call("status")["state"] in ("starting", "running", "stopping"):
        raise SystemExit("An existing run is active; wait for it before starting the campaign")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"pass": False, "finished": False, "runs": [],
              "graphics_scope": "HTTP runner cannot attest browser visibility or GPU load; "
                                "keep edge-lab.html visible and inspect it independently."}
    cases = [("blocked", 10, "normal"), ("failure", 10, "normal"),
             ("overlap", 10, "normal"), ("interfaces", 3, "sensor_demo"),
             ("humans", 10, "normal")]
    for profile, robots, mode in cases:
        state = call("start", {"profile": profile, "robots": robots, "mode": mode, "seed": 0})
        run_id = state["run_id"]
        deadline = time.monotonic() + state["duration_s"] + 30
        while state["state"] in ("starting", "running", "stopping"):
            if time.monotonic() > deadline:
                call("stop")
                report["runs"].append({"profile": profile, "robots": robots,
                                       "pass": False, "error": "evidence window timeout"})
                output.write_text(json.dumps(report, indent=2) + "\n")
                raise TimeoutError(f"{profile} exceeded its bounded window")
            time.sleep(.5)
            state = call("status")
            if state["run_id"] != run_id:
                raise RuntimeError("Run changed externally; refusing mixed evidence")
        raw = state.get("result") or {}
        row = {"profile": profile, "robots": robots, "mode": mode,
               "run_id": run_id, "pass": bool(raw.get("success")),
               "raw": raw, "error": state.get("error")}
        report["runs"].append(row)
        report["finished"] = len(report["runs"]) == len(cases)
        report["pass"] = report["finished"] and all(r["pass"] for r in report["runs"])
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(profile, robots, "PASS" if row["pass"] else "FAIL",
              raw.get("tasks_completed"), raw.get("contacts"), flush=True)
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
